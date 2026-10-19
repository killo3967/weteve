"""Codec TOML de la configuración del demonio: ida y vuelta e importación.

El objetivo del formato es que un mapeo url → channel-id no necesite trucos:
en TOML la url va entrecomillada como clave y cualquier `?`, `&`, `=` o `%`
es parte del texto. El fichero del cliente (``weteve.ini``) es INI aparte y no
lo toca este módulo.
"""

import shutil
from pathlib import Path

import pytest

from src.application.services.list_manager.models import FilterRule
from src.infrastructure.client import config_store
from src.infrastructure.client.config_toml import (
    CLIENT_FILE,
    CONFIG_FILE,
    SCHEMA_VERSION,
    ConfigError,
    catalog_from,
    dumps,
    from_legacy,
    load,
    save,
    target_from_dict,
    target_to_dict,
)

INIVIEJO = Path("K:/weteve/config.ini")


def _config() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "sources": [
            {"id": "s1", "name": "platero", "kind": "m3u",
             "source_type": "file", "location": "C:/listas/platero.m3u",
             "refresh_min": 0, "enabled": True},
            {"id": "e1", "name": "Doble_M", "kind": "epg",
             "source_type": "url",
             "location": "https://x/master/guiatv.xml",
             "refresh_min": 1440, "enabled": False},
        ],
        "targets": [
            {"id": "0", "name": "test", "port": 34400, "bind": "127.0.0.1",
             "starting_number": 1, "custom_m3u": "", "custom_epg": "",
             "epg_source": -1, "m3u_sources": ["s1"],
             "epg_sources": ["e1"],
             "rules": ["group-title:SPAIN:0:and", "custom-filter:!{HBO}:0:and"],
             "disabled": ["http://x/1?h=2&z=&i=3"],
             "overrides": {
                 "http://x/355?h=754f7d&z=&i=132511": "La 1 HD",
                 "http://x/con%20espacio/2": "La 2",
             },
             "quality_priority": {"full hd": 5, "hd": 3}},
        ],
    }


class TestIdaYVuelta:
    def test_lo_que_se_escribe_se_lee_igual(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        cfg = _config()
        save(path, cfg)
        assert load(path) == cfg

    def test_claves_con_url_no_necesitan_trucos(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        save(path, _config())
        crudo = path.read_text(encoding="utf-8")
        assert '"http://x/355?h=754f7d&z=&i=132511" = "La 1 HD"' in crudo
        leido = load(path)["targets"][0]["overrides"]
        assert leido["http://x/355?h=754f7d&z=&i=132511"] == "La 1 HD"
        assert leido["http://x/con%20espacio/2"] == "La 2"

    def test_listas_y_tipos_nativos(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        save(path, _config())
        crudo = path.read_text(encoding="utf-8")
        assert 'rules = ["group-title:SPAIN:0:and", "custom-filter:!{HBO}:0:and"]' in crudo
        assert "refresh_min = 1440" in crudo          # entero, no "1440"
        assert "enabled = false" in crudo             # booleano
        target = load(path)["targets"][0]
        assert target["port"] == 34400
        assert target["epg_source"] == -1
        assert target["quality_priority"] == {"full hd": 5, "hd": 3}

    def test_escapa_comillas_y_barras(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        cfg = _config()
        cfg["targets"][0]["name"] = 'raro "con" \\ barras'
        save(path, cfg)
        assert load(path)["targets"][0]["name"] == 'raro "con" \\ barras'


class TestFicheros:
    def test_fichero_que_no_existe(self, tmp_path: Path):
        vacia = load(tmp_path / CONFIG_FILE)
        assert vacia["sources"] == [] and vacia["targets"] == []

    def test_version_del_futuro_falla_claro(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        path.write_text(f"version = {SCHEMA_VERSION + 1}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="esquema"):
            load(path)

    def test_guardar_dos_veces_deja_bak_y_ningun_temporal(self, tmp_path: Path):
        path = tmp_path / CONFIG_FILE
        save(path, _config())
        primero = path.read_text(encoding="utf-8")
        save(path, _config())
        assert config_store.backup_path(path).read_text(encoding="utf-8") == primero
        assert not (tmp_path / f"{CONFIG_FILE}.tmp").exists()

    def test_nombres_de_fichero(self):
        assert CONFIG_FILE == "daemon.toml"
        assert CLIENT_FILE == "weteve.ini"


class TestAdaptadores:
    """Lo que usan TargetStore y la GUI: dict del fichero ↔ dominio."""

    def test_catalogo_y_targets_van_y_vuelven(self):
        cfg = _config()
        catalog = catalog_from(cfg)
        assert catalog.get("s1") is not None
        assert catalog.get("s1").kind == "m3u"
        target = target_from_dict(cfg["targets"][0], catalog)
        assert target.name == "test"
        assert [ref.id for ref in target.source_refs] == ["s1"]
        assert [ref.id for ref in target.epg_refs] == ["e1"]
        assert target.epg_overrides["http://x/355?h=754f7d&z=&i=132511"] == "La 1 HD"
        assert target.rules and isinstance(target.rules[0], FilterRule)
        assert target.quality_priority == {"full hd": 5, "hd": 3}

        vuelta = target_to_dict(target, "0")
        assert vuelta["overrides"] == cfg["targets"][0]["overrides"]
        assert vuelta["rules"] == cfg["targets"][0]["rules"]
        assert vuelta["disabled"] == cfg["targets"][0]["disabled"]

    def test_ref_a_fuente_inexistente_se_ignora(self):
        cfg = _config()
        cfg["targets"][0]["m3u_sources"] = ["fantasma"]
        target = target_from_dict(cfg["targets"][0], catalog_from(cfg))
        assert target.source_refs == ()


@pytest.mark.skipif(not INIVIEJO.exists(), reason="sin config.ini real")
class TestImportacionDelIni:
    """La migración vuelca el INI real sin reinterpretarlo."""

    def _copia(self, tmp_path: Path) -> Path:
        destino = tmp_path / "config.ini"
        shutil.copy2(INIVIEJO, destino)
        return destino

    def test_convierte_cartera_y_targets(self, tmp_path: Path):
        cfg = from_legacy(self._copia(tmp_path))
        assert cfg["version"] == SCHEMA_VERSION
        assert len(cfg["sources"]) >= 2
        assert {s["kind"] for s in cfg["sources"]} == {"m3u", "epg"}
        assert len(cfg["targets"]) == 1
        target = cfg["targets"][0]
        assert target["id"] == "0"
        assert target["overrides"]                     # dict url -> id
        assert all("//" in url for url in target["overrides"])

    def test_la_ida_y_vuelta_conserva_los_mapeos(self, tmp_path: Path):
        cfg = from_legacy(self._copia(tmp_path))
        path = tmp_path / CONFIG_FILE
        save(path, cfg)
        assert load(path)["targets"][0]["overrides"] == cfg["targets"][0]["overrides"]

    def test_dos_importaciones_dan_lo_mismo(self, tmp_path: Path):
        ini = self._copia(tmp_path)
        assert from_legacy(ini) == from_legacy(ini)

    def test_el_toml_generado_se_puede_volver_a_leer(self, tmp_path: Path):
        cfg = from_legacy(self._copia(tmp_path))
        path = tmp_path / CONFIG_FILE
        save(path, cfg)
        assert dumps(load(path)) == dumps(cfg)


@pytest.mark.skipif(not INIVIEJO.exists(), reason="sin config.ini real")
class TestComandoMigrar:
    """`daemon.py migrate-config` escribe los dos ficheros y verifica."""

    def _ini(self, tmp_path: Path) -> Path:
        ini = tmp_path / "config.ini"
        shutil.copy2(INIVIEJO, ini)
        return ini

    def test_escribe_datos_y_cliente(self, tmp_path: Path, capsys):
        import daemon as daemon_mod

        ini = self._ini(tmp_path)
        assert daemon_mod.cmd_migrate_config(ini) == 0
        assert (tmp_path / CONFIG_FILE).exists()
        assert (tmp_path / CLIENT_FILE).exists()
        assert ini.exists()                       # no se borra el original
        salida = capsys.readouterr().out
        assert "migrado" in salida and "map" in salida.lower()

    def test_el_cliente_migrado_conserva_el_puerto(self, tmp_path: Path):
        import daemon as daemon_mod
        from src.application.services.daemon.config import load_daemon_config

        ini = self._ini(tmp_path)
        daemon_mod.cmd_migrate_config(ini)
        original = load_daemon_config(ini)
        migrado = load_daemon_config(tmp_path / CLIENT_FILE)
        assert migrado == original

    def test_check_verifica_que_coincide(self, tmp_path: Path):
        import daemon as daemon_mod

        ini = self._ini(tmp_path)
        daemon_mod.cmd_migrate_config(ini)
        assert daemon_mod.cmd_migrate_config(ini, check=True) == 0
        datos = load(tmp_path / CONFIG_FILE)
        datos["targets"][0]["name"] = "otro"
        save(tmp_path / CONFIG_FILE, datos)
        assert daemon_mod.cmd_migrate_config(ini, check=True) == 1

    def test_sin_ini_avisa(self, tmp_path: Path, capsys):
        import daemon as daemon_mod

        assert daemon_mod.cmd_migrate_config(tmp_path / "no.ini") == 1
        assert "no hay" in capsys.readouterr().out
