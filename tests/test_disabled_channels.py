"""Tests: disabled_channels en modelo, pipeline y persistencia."""

from pathlib import Path

from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import (
    PipelineTarget,
)


def test_target_default_empty():
    t = PipelineTarget(name="x", source_index="")
    assert t.disabled_channels == ()


def _store(tmp: Path) -> TargetStore:
    store = TargetStore(tmp / "config.ini")
    store.load()
    return store


def test_store_roundtrip_disabled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = PipelineTarget(
        name="t", source_index="/x.m3u", port=8081,
        disabled_channels=("http://a/1", "http://a/2"),
    )
    store.save("0", t)
    store2 = _store(tmp_path)
    loaded = store2.get("0")
    assert loaded is not None
    assert loaded.disabled_channels == ("http://a/1", "http://a/2")


def test_store_roundtrip_no_disabled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("0", PipelineTarget(name="t", source_index="/x.m3u"))
    store2 = _store(tmp_path)
    loaded = store2.get("0")
    assert loaded is not None
    assert loaded.disabled_channels == ()


def test_store_overwrite_disabled(tmp_path: Path) -> None:
    """Reactivar un canal (lista vacía) borra la clave persistida."""
    store = _store(tmp_path)
    store.save("0", PipelineTarget(name="t", source_index="/x.m3u",
                                   disabled_channels=("u1",)))
    store.save("0", PipelineTarget(name="t", source_index="/x.m3u"))
    store2 = _store(tmp_path)
    loaded = store2.get("0")
    assert loaded is not None
    assert loaded.disabled_channels == ()
