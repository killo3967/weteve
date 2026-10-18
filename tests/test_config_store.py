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
