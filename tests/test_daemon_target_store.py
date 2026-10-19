"""Tests del TargetStore: CRUD persistente de targets en ``daemon.toml``.

El formato vive en ``config_toml``; aquí se comprueba el CRUD: cargar, guardar
sin tocar otros targets, borrar, releer desde disco y las selecciones de
cartera (``m3u_sources`` / ``epg_sources``).
"""

from dataclasses import replace as dataclasses_replace
from pathlib import Path

from toml_helpers import source, target, write_daemon

from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.infrastructure.client import config_toml


def _seed(path: Path) -> None:
    """``daemon.toml`` con un target existente."""
    write_daemon(path, targets=[target(
        "0", name="Existente", custom_m3u="/lists/existing.m3u",
        custom_epg="/lists/existing.xml", epg_source=-1,
        rules=["group-title:{Deportes}:0"],
        overrides={"http://s/1": "ch1"}, port=8080, bind="127.0.0.1")])


def _raw(path: Path, target_id: str = "0") -> dict:
    """Bloque del target tal como quedó en el fichero."""
    for raw in config_toml.load(path)["targets"]:
        if str(raw.get("id")) == target_id:
            return raw
    raise AssertionError(f"target {target_id} no está en {path}")


class TestTargetStore:
    def test_load_existing_target(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
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
        path = tmp_path / "daemon.toml"
        _seed(path)
        store = TargetStore(path)
        store.load()
        store.save("1", PipelineTarget(name="Nuevo", source_index="/n.m3u",
                                       port=8081),
                   epg_path="/n.xml", bind="0.0.0.0")

        assert _raw(path, "0")["name"] == "Existente"
        nuevo = _raw(path, "1")
        assert nuevo["name"] == "Nuevo"
        assert nuevo["port"] == 8081
        assert nuevo["bind"] == "0.0.0.0"

    def test_save_updates_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
        _seed(path)
        store = TargetStore(path)
        store.load()
        store.save("0", PipelineTarget(name="Editada", source_index="/x.m3u",
                                       port=9090))
        t = store.get("0")
        assert t is not None and t.name == "Editada" and t.port == 9090
        assert _raw(path, "0")["name"] == "Editada"

    def test_save_serializes_rules_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
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
            quality_priority={"hd": 4},
        ))
        store2 = TargetStore(path)
        store2.load()
        t = store2.get("2")
        assert t is not None
        assert t.rules == rules
        assert t.epg_overrides == {"http://a": "cid"}
        assert t.quality_priority == {"hd": 4}

    def test_delete_removes_target(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
        _seed(path)
        store = TargetStore(path)
        store.load()
        assert store.delete("0") is True
        assert store.get("0") is None
        assert config_toml.load(path)["targets"] == []

    def test_delete_unknown_false(self, tmp_path: Path) -> None:
        store = TargetStore(tmp_path / "daemon.toml")
        store.load()
        assert store.delete("ghost") is False

    def test_load_without_file(self, tmp_path: Path) -> None:
        store = TargetStore(tmp_path / "no-existe.toml")
        store.load()
        assert store.list() == {}


class TestTargetStoreSelections:
    """Selección de la cartera: m3u_sources / epg_sources por target."""

    def _con_cartera(self, path: Path) -> None:
        write_daemon(
            path,
            sources=[source("aa", "platero", "m3u", "C:/listas/platero.m3u"),
                     source("bb", "TV", "epg", "C:/e.xml")],
            targets=[target("0", name="Existente",
                            m3u_sources=["aa"], epg_sources=["bb"],
                            custom_epg="/lists/existing.xml")],
        )

    def test_load_mapea_selecciones_a_source_refs(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
        self._con_cartera(path)
        store = TargetStore(path)
        store.load()
        t = store.get("0")
        assert t is not None
        assert t.source_refs == (
            SourceRef("aa", "platero", "C:/listas/platero.m3u"),)
        assert t.epg_refs == (SourceRef("bb", "TV", "C:/e.xml"),)

    def test_save_con_refs_persiste_selecciones(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.toml"
        self._con_cartera(path)
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
        raw = _raw(path, "0")
        assert raw["m3u_sources"] == ["aa"]
        assert raw["epg_sources"] == ["bb"]
        assert raw["custom_m3u"] == ""      # con cartera, el M3U suelto sobra

    def test_save_sin_refs_borra_selecciones_viejas(self, tmp_path: Path) -> None:
        """Un target vuelve a fuente única => sus selecciones no quedan muertas."""
        path = tmp_path / "daemon.toml"
        self._con_cartera(path)
        store = TargetStore(path)
        store.load()
        base = store.get("0")
        assert base is not None
        store.save("0", dataclasses_replace(base, source_refs=(), epg_refs=()))
        raw = _raw(path, "0")
        assert raw["m3u_sources"] == []
        assert raw["epg_sources"] == []
        assert raw["custom_m3u"] == base.source_index

    def test_refs_huerfanos_ignorados(self, tmp_path: Path) -> None:
        """Si la fila de cartera ya no existe, el ref se descarta al cargar."""
        path = tmp_path / "daemon.toml"
        write_daemon(path, sources=[],
                     targets=[target("0", m3u_sources=["fantasma", "aa"])])
        store = TargetStore(path)
        store.load()
        t = store.get("0")
        assert t is not None
        assert t.source_refs == ()
