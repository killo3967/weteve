"""Serialización única de config.ini (config_store).

Antes había cuatro serializadores (persistencia, TargetStore, sources_store y
daemon.config) y podían divergir: los tests fijan que el formato salga del
mismo sitio y que cada guardado escriba una sola vez.
"""

from pathlib import Path

from src.application.services.daemon.config import DaemonConfig, save_daemon_config
from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
)
from src.infrastructure.client import config_store
from src.infrastructure.client.persistencia import load_config
from src.infrastructure.client.sources_store import read_catalog, save_catalog

BASE = """[listmanager.target.0]
name = viejo
custom_m3u = C:/viejo.m3u
port = 8080
bind = 127.0.0.1
rules =
epg_overrides =
disabled_channels =
m3u_sources =
epg_sources =

[source.s1]
name = platero
kind = m3u
source_type = file
location = C:/platero.m3u
refresh_min = 0
enabled = 1
"""


def _entry(eid="s1", name="platero", kind="m3u", loc="C:/platero.m3u"):
    return SourceEntry(eid, name, kind, "file", loc, 0, True)


def _target(**kw) -> PipelineTarget:
    data = {"name": "nuevo", "source_index": "C:/nuevo.m3u", "port": 9999,
            "rules": (FilterRule("group-title", "SPAIN", operator="and"),),
            "epg_overrides": {"http://s/a": "a.tv"},
            "disabled_channels": ("http://s/b",)}
    data.update(kw)
    return PipelineTarget(**data)


def _section(path: Path, section: str) -> dict:
    import configparser

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return dict(parser[section])


class TestUnSoloSerializador:
    def test_target_store_y_persistencia_escriben_igual(self, tmp_path):
        a = tmp_path / "a.ini"
        b = tmp_path / "b.ini"
        a.write_text(BASE, encoding="utf-8")
        b.write_text(BASE, encoding="utf-8")
        catalog = read_catalog(config_store.parse(a))
        ref = SourceRef("s1", "platero", "C:/platero.m3u")

        TargetStore(a).save(
            "0", _target(source_refs=(ref,)), epg_path="C:/tv.xml")
        config_store.update(b, lambda parser: config_store.write_target(
            parser, 0,
            name="nuevo", m3u="C:/nuevo.m3u", epg_path="C:/tv.xml",
            epg_source=None,
            rules=[FilterRule("group-title", "SPAIN", operator="and")],
            overrides={"http://s/a": "a.tv"},
            disabled=["http://s/b"],
            port=9999, bind="127.0.0.1",
            m3u_refs=[ref], epg_refs=[],
            catalog=read_catalog(parser)))

        assert _section(a, "listmanager.target.0") == \
            _section(b, "listmanager.target.0")
        assert catalog.get("s1") is not None

    def test_cada_guardado_escribe_una_vez(self, tmp_path, monkeypatch):
        path = tmp_path / "c.ini"
        path.write_text(BASE, encoding="utf-8")
        calls = []
        real = config_store.dump

        def spy(parser, p):
            calls.append(1)
            return real(parser, p)

        monkeypatch.setattr(config_store, "dump", spy)
        save_catalog(path, SourceCatalog([_entry()]))
        assert calls == [1]
        TargetStore(path).save("0", _target())
        assert calls == [1, 1]
        save_daemon_config(path, DaemonConfig())
        assert calls == [1, 1, 1]

    def test_reglas_overrides_y_desactivados_round_trip(self, tmp_path):
        path = tmp_path / "d.ini"
        path.write_text(BASE, encoding="utf-8")
        TargetStore(path).save("0", _target())
        cfg = load_config(path)["targets"][0]
        assert cfg["rules"][0].pattern == "SPAIN"
        assert cfg["rules"][0].operator == "and"
        assert cfg["epg_overrides"] == {"http://s/a": "a.tv"}
        assert cfg["disabled_channels"] == ["http://s/b"]

    def test_carpeta_destino_se_crea(self, tmp_path):
        path = tmp_path / "sub" / "config.ini"
        save_catalog(path, SourceCatalog([_entry()]))
        assert read_catalog(config_store.parse(path)).get("s1") is not None


class TestPreservarSecciones:
    def test_guardar_cartera_no_toca_targets_ni_daemon(self, tmp_path):
        path = tmp_path / "e.ini"
        path.write_text(BASE + "\n[DAEMON]\ncontrol_port = 34400\n",
                        encoding="utf-8")
        save_catalog(path, SourceCatalog([_entry()]))
        assert _section(path, "listmanager.target.0")["name"] == "viejo"
        assert _section(path, "DAEMON")["control_port"] == "34400"

    def test_guardar_daemon_no_toca_targets(self, tmp_path):
        path = tmp_path / "f.ini"
        path.write_text(BASE, encoding="utf-8")
        save_daemon_config(path, DaemonConfig(control_port=34500,
                                              mcp_token="tk"))
        assert _section(path, "listmanager.target.0")["name"] == "viejo"
        assert _section(path, "DAEMON")["mcp_token"] == "tk"

    def test_quitar_fuentes_del_catalogo_las_borra(self, tmp_path):
        path = tmp_path / "g.ini"
        path.write_text(BASE, encoding="utf-8")
        save_catalog(path, SourceCatalog([_entry("s2", "otra")]))
        parser = config_store.parse(path)
        assert [s for s in parser.sections() if s.startswith("source.")] == \
            ["source.s2"]

    def test_ref_fuera_de_la_cartera_no_se_persiste(self, tmp_path):
        path = tmp_path / "h.ini"
        path.write_text(BASE, encoding="utf-8")
        TargetStore(path).save(
            "0", _target(source_refs=(SourceRef("fantasma", "x", "C:/x.m3u"),)))
        assert _section(path, "listmanager.target.0")["m3u_sources"] == ""


class TestStartingNumber:
    """starting_number se persiste: la API lo acepta y lo devuelve."""

    def test_round_trip_por_target_store(self, tmp_path):
        path = tmp_path / "n.ini"
        path.write_text(BASE, encoding="utf-8")
        TargetStore(path).save("0", _target(starting_number=500))
        assert load_config(path)["targets"][0]["starting_number"] == 500
        assert TargetStore(path).reload("0").starting_number == 500

    def test_default_1_si_no_esta(self, tmp_path):
        path = tmp_path / "o.ini"
        path.write_text(BASE, encoding="utf-8")
        assert load_config(path)["targets"][0]["starting_number"] == 1
        assert TargetStore(path).reload("0").starting_number == 1


class TestEscrituraSegura:
    """config.ini no está en git: una escritura a medias lo perdería todo."""

    def _fichero(self, tmp_path: Path) -> Path:
        path = tmp_path / "config.ini"
        path.write_text("[DAEMON]\ncontrol_port = 34400\n", encoding="utf-8")
        return path

    def test_guarda_la_version_anterior_en_bak(self, tmp_path):
        path = self._fichero(tmp_path)
        anterior = path.read_text(encoding="utf-8")

        config_store.update(path, lambda p: p.set("DAEMON", "control_port",
                                                 "35000"))

        assert "35000" in path.read_text(encoding="utf-8")
        bak = config_store.backup_path(path)
        assert bak.read_text(encoding="utf-8") == anterior

    def test_no_deja_temporales(self, tmp_path):
        path = self._fichero(tmp_path)
        config_store.update(path, lambda p: p.set("DAEMON", "mcp_token", "x"))
        assert not (tmp_path / "config.ini.tmp").exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "config.ini", "config.ini.bak"]

    def test_un_fallo_a_mitad_deja_el_fichero_intacto(self, tmp_path,
                                                     monkeypatch):
        path = self._fichero(tmp_path)
        anterior = path.read_text(encoding="utf-8")

        def fsync_roto(_fd):
            raise OSError("disco lleno")

        monkeypatch.setattr(config_store.os, "fsync", fsync_roto)
        import pytest

        with pytest.raises(OSError):
            config_store.update(path, lambda p: p.set("DAEMON", "mcp_token",
                                                      "nuevo"))
        assert path.read_text(encoding="utf-8") == anterior
        assert not (tmp_path / "config.ini.tmp").exists()

    def test_primera_escritura_sin_bak(self, tmp_path):
        path = tmp_path / "nuevo.ini"
        config_store.update(path, lambda p: p.add_section("DAEMON"))
        assert path.exists()
        assert not config_store.backup_path(path).exists()

    def test_borrar_un_target_usa_la_escritura_segura(self, tmp_path):
        path = self._fichero(tmp_path)
        store = TargetStore(path)
        store.load()
        store.save("9", _target(), epg_path=None)
        assert "[listmanager.target.9]" in path.read_text(encoding="utf-8")

        assert store.delete("9") is True
        assert "[listmanager.target.9]" not in path.read_text(encoding="utf-8")
        assert config_store.backup_path(path).exists()


class TestFormatoDeMapeos:
    """`epg_overrides` legible: un mapeo por línea, y el viejo se sigue leyendo.

    Una sección con la url como clave no vale: configparser parte las opciones
    por ``=`` y ``:``, y las urls llevan los dos.
    """

    def _escribe(self, path: Path, overrides: dict[str, str]) -> None:
        config_store.update(path, lambda p: config_store.write_target(
            p, 0, name="t", m3u="C:/l.m3u", overrides=overrides))

    def test_un_mapeo_por_linea(self, tmp_path: Path):
        path = tmp_path / "config.ini"
        self._escribe(path, {"http://a/1": "es.uno", "http://a/2": "es.dos"})
        crudo = path.read_text(encoding="utf-8")
        lineas = [linea.strip() for linea in crudo.splitlines()]
        assert ";;" not in crudo
        assert "http://a/1::es.uno" in lineas
        assert "http://a/2::es.dos" in lineas

    def test_ida_y_vuelta(self, tmp_path: Path):
        path = tmp_path / "config.ini"
        overrides = {"http://a/1?h=x&z=&i=1": "es.uno", "http://a/2": "es.dos"}
        self._escribe(path, overrides)
        leido = load_config(path)["targets"][0]["epg_overrides"]
        assert leido == overrides

    def test_el_formato_viejo_de_una_linea_se_lee(self, tmp_path: Path):
        path = tmp_path / "config.ini"
        path.write_text(
            "[listmanager.target.0]\nname = t\n"
            "epg_overrides = http://a/1::es.uno;;http://a/2::es.dos\n",
            encoding="utf-8")
        leido = load_config(path)["targets"][0]["epg_overrides"]
        assert leido == {"http://a/1": "es.uno", "http://a/2": "es.dos"}

    def test_una_url_con_porcentaje_sobrevive(self, tmp_path: Path):
        """Sin interpolación: ``%20`` en una ruta no es un error de config."""
        path = tmp_path / "config.ini"
        overrides = {"http://a/mi%20lista.m3u": "es.uno"}
        self._escribe(path, overrides)
        assert load_config(path)["targets"][0]["epg_overrides"] == overrides

    def test_quitar_un_mapeo_lo_borra_del_fichero(self, tmp_path: Path):
        path = tmp_path / "config.ini"
        self._escribe(path, {"http://a/1": "es.uno", "http://a/2": "es.dos"})
        self._escribe(path, {"http://a/2": "es.dos"})
        crudo = path.read_text(encoding="utf-8")
        assert "http://a/1" not in crudo
        assert load_config(path)["targets"][0]["epg_overrides"] == {
            "http://a/2": "es.dos"}

    def test_target_store_y_la_gui_leen_lo_mismo(self, tmp_path: Path):
        """Un solo lector: el demonio y la GUI no pueden divergir."""
        path = tmp_path / "config.ini"
        overrides = {"http://a/1?h=1": "es.uno"}
        self._escribe(path, overrides)
        store = TargetStore(path)
        store.load()
        target = store.get("0")
        assert target is not None
        assert target.epg_overrides == load_config(
            path)["targets"][0]["epg_overrides"] == overrides
