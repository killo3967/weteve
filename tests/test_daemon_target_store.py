"""Tests del TargetStore: CRUD persistente de targets en config.ini.

Extrae la lógica load_config/save_config de main.py para que daemon, UI y MCP
compartan la misma lectura/escritura sin duplicación.
"""

import configparser
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)


def _seed(path: Path) -> None:
    """config.ini con un target existente (formato main.py)."""
    parser = configparser.ConfigParser()
    parser["listmanager.target.0"] = {
        "name": "Existente",
        "custom_m3u": "/lists/existing.m3u",
        "custom_epg": "/lists/existing.xml",
        "epg_source": "-1",
        "rules": "group-title:{Deportes}:0",
        "epg_overrides": "http://s/1::ch1",
        "port": "8080",
        "bind": "127.0.0.1",
    }
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


class TestTargetStore:
    def test_load_existing_target(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        store = TargetStore(path)
        assert store.list() == {}  # no cargado todavía

        store.load()
        t = store.get("0")
        assert t is not None
        assert t.name == "Existente"
        assert t.source_index == "/lists/existing.m3u"
        assert t.rules == (FilterRule(field="group-title", pattern="{Deportes}"),)
        assert t.epg_overrides == {"http://s/1": "ch1"}
        assert t.port == 8080
        assert store.get_epg_path("0") == "/lists/existing.xml"

    def test_save_new_target_preserves_others(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        store = TargetStore(path)
        store.load()
        store.save("1", PipelineTarget(name="Nuevo", source_index="/n.m3u", port=8081),
                   epg_path="/n.xml", bind="0.0.0.0")

        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser["listmanager.target.0"]["name"] == "Existente"
        assert parser["listmanager.target.1"]["name"] == "Nuevo"
        assert parser["listmanager.target.1"]["port"] == "8081"
        assert parser["listmanager.target.1"]["bind"] == "0.0.0.0"

    def test_save_updates_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        store = TargetStore(path)
        store.load()
        store.save("0", PipelineTarget(name="Editada", source_index="/x.m3u", port=9090))
        t = store.get("0")
        assert t is not None and t.name == "Editada" and t.port == 9090
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser["listmanager.target.0"]["name"] == "Editada"

    def test_save_serializes_rules_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        store = TargetStore(path)
        store.load()
        rules = (
            FilterRule(field="group-title", pattern="{Deportes}"),
            FilterRule(field="custom-filter", pattern="!{Adultos}", exclude=True),
        )
        store.save("2", PipelineTarget(
            name="Con reglas", source_index="/r.m3u", port=8082, rules=rules,
            epg_overrides={"http://a": "cid"},
        ))
        store2 = TargetStore(path)
        store2.load()
        t = store2.get("2")
        assert t is not None
        assert t.rules == rules
        assert t.epg_overrides == {"http://a": "cid"}

    def test_delete_removes_section(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        store = TargetStore(path)
        store.load()
        assert store.delete("0") is True
        assert store.get("0") is None
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert not parser.has_section("listmanager.target.0")

    def test_delete_unknown_false(self, tmp_path: Path) -> None:
        store = TargetStore(tmp_path / "config.ini")
        store.load()
        assert store.delete("ghost") is False

    def test_load_without_file(self, tmp_path: Path) -> None:
        store = TargetStore(tmp_path / "nope.ini")
        store.load()
        assert store.list() == {}


class TestTargetStoreSelections:
    """Selección de la cartera: m3u_sources / epg_sources por target."""

    def test_load_mapea_selecciones_a_source_refs(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["source.aa"] = {"name": "platero", "kind": "m3u",
                               "source_type": "file",
                               "location": "C:/listas/platero.m3u",
                               "refresh_min": "60", "enabled": "1"}
        parser["source.bb"] = {"name": "TV", "kind": "epg", "source_type": "file",
                               "location": "C:/e.xml", "refresh_min": "0",
                               "enabled": "1"}
        parser["listmanager.target.0"]["m3u_sources"] = "aa"
        parser["listmanager.target.0"]["epg_sources"] = "bb"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        store = TargetStore(path)
        store.load()
        t = store.get("0")
        assert t.source_refs == (SourceRef("aa", "platero", "C:/listas/platero.m3u"),)
        assert t.epg_refs == (SourceRef("bb", "TV", "C:/e.xml"),)

    def test_save_con_refs_persiste_selecciones(self, tmp_path: Path) -> None:
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["source.aa"] = {"name": "platero", "kind": "m3u",
                               "source_type": "file", "location": "C:/l.m3u",
                               "refresh_min": "60", "enabled": "1"}
        parser["source.bb"] = {"name": "TV", "kind": "epg",
                               "source_type": "file", "location": "C:/e.xml",
                               "refresh_min": "0", "enabled": "1"}
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        store = TargetStore(path)
        store.load()
        base = store.get("0")
        assert base is not None
        t = dataclasses_replace(
            base,
            source_refs=(SourceRef("aa", "platero", "C:/l.m3u"),),
            epg_refs=(SourceRef("bb", "TV", "C:/e.xml"),),
        )
        store.save("0", t, epg_path="/lists/existing.xml")
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser.get("listmanager.target.0", "m3u_sources") == "aa"
        assert parser.get("listmanager.target.0", "epg_sources") == "bb"

    def test_save_con_refs_resuelve_location_desde_la_cartera(self, tmp_path: Path) -> None:
        """Los refs se serializan con location aunque venga sin ella."""
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["source.aa"] = {"name": "platero", "kind": "m3u",
                               "source_type": "url",
                               "location": "http://s/l.m3u",
                               "refresh_min": "30", "enabled": "1"}
        parser["listmanager.target.0"]["m3u_sources"] = "aa"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)

        store = TargetStore(path)
        store.load()
        base = store.get("0")
        assert base is not None
        t = dataclasses_replace(base)
        store.save("0", t)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser.get("listmanager.target.0", "m3u_sources") == "aa"

    def test_save_sin_refs_borra_selecciones_viejas(self, tmp_path: Path) -> None:
        """Un target vuelve a fuente única => sus selecciones no quedan muertas."""
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["listmanager.target.0"]["m3u_sources"] = "aa"
        parser["listmanager.target.0"]["epg_sources"] = "bb"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)
        store = TargetStore(path)
        store.load()
        store.save("0", store.get("0"))
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        assert parser.get("listmanager.target.0", "m3u_sources", fallback="") == ""
        assert parser.get("listmanager.target.0", "epg_sources", fallback="") == ""

    def test_refs_huerfanos_ignorados(self, tmp_path: Path) -> None:
        """Si la fila de cartera ya no existe, el ref se descarta al cargar."""
        path = tmp_path / "config.ini"
        _seed(path)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        parser["listmanager.target.0"]["m3u_sources"] = "fantasma,aa"
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)
        store = TargetStore(path)
        store.load()
        # 'aa' no existe => no queda ningún ref válido
        assert store.get("0").source_refs == ()
