"""Codec TOML de la configuración: ida y vuelta, claves con url e importación.

El objetivo del formato es que un mapeo url → channel-id no necesite trucos:
en TOML la url va entrecomillada como clave y cualquier `?`, `&`, `=` o `%`
es parte del texto.
"""

import shutil
from pathlib import Path

import pytest

from src.infrastructure.client import config_store
from src.infrastructure.client.config_toml import (
    SCHEMA_VERSION,
    ConfigError,
    dumps,
    dumps_daemon,
    from_legacy,
    load,
    load_daemon,
    save,
    save_daemon,
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
        path = tmp_path / "config.toml"
        cfg = _config()
        save(path, cfg)
        assert load(path) == cfg

    def test_claves_con_url_no_necesitan_trucos(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        save(path, _config())
        crudo = path.read_text(encoding="utf-8")
        assert '"http://x/355?h=754f7d&z=&i=132511" = "La 1 HD"' in crudo
        leido = load(path)["targets"][0]["overrides"]
        assert leido["http://x/355?h=754f7d&z=&i=132511"] == "La 1 HD"
        assert leido["http://x/con%20espacio/2"] == "La 2"

    def test_listas_y_tipos_nativos(self, tmp_path: Path):
        path = tmp_path / "config.toml"
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
        path = tmp_path / "config.toml"
        cfg = _config()
        cfg["targets"][0]["name"] = 'raro "con" \\ barras'
        save(path, cfg)
        assert load(path)["targets"][0]["name"] == 'raro "con" \\ barras'

    def test_daemon_toml(self, tmp_path: Path):
        path = tmp_path / "daemon.toml"
        daemon = {"version": SCHEMA_VERSION, "host": "127.0.0.1", "port": 34990,
                  "mcp_port": 34990, "token": "secreto", "autostart": True,
                  "stop_on_exit": False}
        save_daemon(path, daemon)
        assert load_daemon(path) == daemon


class TestFicheros:
    def test_fichero_que_no_existe(self, tmp_path: Path):
        vacia = load(tmp_path / "no.toml")
        assert vacia["sources"] == [] and vacia["targets"] == []
        assert load_daemon(tmp_path / "no.toml")["port"] == 34400

    def test_version_del_futuro_falla_claro(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        path.write_text(f"version = {SCHEMA_VERSION + 1}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="esquema"):
            load(path)

    def test_guardar_dos_veces_deja_bak_y_ningun_temporal(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        save(path, _config())
        primero = path.read_text(encoding="utf-8")
        save(path, _config())
        assert config_store.backup_path(path).read_text(encoding="utf-8") == primero
        assert not (tmp_path / "config.toml.tmp").exists()


@pytest.mark.skipif(not INIVIEJO.exists(), reason="sin config.ini real")
class TestImportacionDelIni:
    """La migración vuelca el INI real sin reinterpretarlo."""

    def _copia(self, tmp_path: Path) -> Path:
        destino = tmp_path / "config.ini"
        shutil.copy2(INIVIEJO, destino)
        return destino

    def test_convierte_cartera_targets_y_daemon(self, tmp_path: Path):
        cfg, daemon = from_legacy(self._copia(tmp_path))
        assert cfg["version"] == SCHEMA_VERSION
        assert len(cfg["sources"]) >= 2
        assert {s["kind"] for s in cfg["sources"]} == {"m3u", "epg"}
        assert len(cfg["targets"]) == 1
        target = cfg["targets"][0]
        assert target["id"] == "0"
        assert target["overrides"]                     # dict url -> id
        assert all("//" in url for url in target["overrides"])
        assert daemon["port"] == 34400

    def test_la_ida_y_vuelta_conserva_los_mapeos(self, tmp_path: Path):
        cfg, _ = from_legacy(self._copia(tmp_path))
        path = tmp_path / "config.toml"
        save(path, cfg)
        assert load(path)["targets"][0]["overrides"] == cfg["targets"][0]["overrides"]

    def test_dos_importaciones_dan_lo_mismo(self, tmp_path: Path):
        ini = self._copia(tmp_path)
        assert from_legacy(ini) == from_legacy(ini)

    def test_el_toml_generado_se_puede_volver_a_leer(self, tmp_path: Path):
        cfg, daemon = from_legacy(self._copia(tmp_path))
        path = tmp_path / "config.toml"
        save(path, cfg)
        save_daemon(tmp_path / "daemon.toml", daemon)
        assert dumps(load(path)) == dumps(cfg)
        assert dumps_daemon(load_daemon(tmp_path / "daemon.toml")) == \
            dumps_daemon(daemon)


@pytest.mark.skipif(not INIVIEJO.exists(), reason="sin config.ini real")
class TestComandoMigrar:
    """`daemon.py migrate-config` convierte y se puede verificar."""

    def test_escribe_los_dos_ficheros(self, tmp_path: Path, capsys):
        import daemon as daemon_mod

        ini = tmp_path / "config.ini"
        shutil.copy2(INIVIEJO, ini)
        assert daemon_mod.cmd_migrate_config(ini) == 0
        assert (tmp_path / "config.toml").exists()
        assert (tmp_path / "daemon.toml").exists()
        assert ini.exists()                       # no se borra el original
        salida = capsys.readouterr().out
        assert "migrado" in salida and "map" in salida.lower()

    def test_check_verifica_que_coincide(self, tmp_path: Path):
        import daemon as daemon_mod

        ini = tmp_path / "config.ini"
        shutil.copy2(INIVIEJO, ini)
        daemon_mod.cmd_migrate_config(ini)
        assert daemon_mod.cmd_migrate_config(ini, check=True) == 0
        # si el TOML se toca, deja de coincidir
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(toml_path.read_text(encoding="utf-8").replace(
            "version = 2", "version = 2\n# tocado"), encoding="utf-8")
        assert daemon_mod.cmd_migrate_config(ini, check=True) == 0  # comentario
        datos = load(toml_path)
        datos["targets"][0]["name"] = "otro"
        save(toml_path, datos)
        assert daemon_mod.cmd_migrate_config(ini, check=True) == 1

    def test_sin_ini_avisa(self, tmp_path: Path, capsys):
        import daemon as daemon_mod

        assert daemon_mod.cmd_migrate_config(tmp_path / "no.ini") == 1
        assert "no hay" in capsys.readouterr().out
