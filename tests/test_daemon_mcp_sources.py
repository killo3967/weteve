"""Herramientas MCP para la cartera de fuentes (CRUD + selección del target)."""

from pathlib import Path

import pytest
from toml_helpers import target, write_daemon

from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore


@pytest.fixture
def env(tmp_path: Path):
    cfg = write_daemon(tmp_path / "daemon.toml",
                       targets=[target("0", name="principal")])
    store = TargetStore(cfg)
    store.load()
    reg = build_tool_registry(TargetSupervisor(catalog_fn=lambda: _load(cfg)),
                              store)
    return reg, store, cfg


def _load(path: Path):
    from src.infrastructure.client.sources_store import load_catalog
    return load_catalog(path)


def _call(reg, name, args):
    tool = reg[name]
    return tool["handler"](**args)


class TestCatalogoMcp:
    def test_add_y_list_sources(self, env):
        reg, _, _ = env
        out = _call(reg, "add_source", {
            "name": "principal", "kind": "m3u",
            "location": "http://s/l.m3u", "refresh_min": 30,
        })
        assert "id" in out and out.get("error") is None
        listing = _call(reg, "list_sources", {})["sources"]
        assert len(listing) == 1
        assert listing[0]["name"] == "principal"
        assert listing[0]["kind"] == "m3u"
        assert listing[0]["refresh_min"] == 30

    def test_list_sources_filtra_por_kind(self, env):
        reg, _, _ = env
        _call(reg, "add_source", {"name": "a", "kind": "m3u",
                                  "location": "http://s/a.m3u"})
        _call(reg, "add_source", {"name": "g", "kind": "epg",
                                  "location": "C:/g.xml"})
        assert len(_call(reg, "list_sources", {"kind": "epg"})["sources"]) == 1

    def test_add_source_valida_kind(self, env):
        reg, _, _ = env
        out = _call(reg, "add_source", {"name": "x", "kind": "rss",
                                        "location": "http://x"})
        assert "error" in out

    def test_update_source(self, env):
        reg, _, _ = env
        sid = _call(reg, "add_source", {
            "name": "a", "kind": "m3u", "location": "C:/a.m3u"})["id"]
        out = _call(reg, "update_source", {"id": sid, "name": "renamed",
                                           "refresh_min": 15})
        assert out.get("ok")
        src = _call(reg, "list_sources", {})["sources"][0]
        assert src["name"] == "renamed" and src["refresh_min"] == 15

    def test_remove_source(self, env):
        reg, _, _ = env
        sid = _call(reg, "add_source", {
            "name": "a", "kind": "m3u", "location": "C:/a.m3u"})["id"]
        assert _call(reg, "remove_source", {"id": sid}).get("ok")
        assert _call(reg, "list_sources", {})["sources"] == []

    def test_persistencia_en_daemon_toml(self, env):
        reg, _, cfg = env
        _call(reg, "add_source", {"name": "a", "kind": "m3u",
                                  "location": "C:/a.m3u"})
        from src.infrastructure.client import config_toml

        fuentes = config_toml.load(cfg)["sources"]
        assert len(fuentes) == 1 and fuentes[0]["name"] == "a"


class TestSeleccionTargetMcp:
    def test_set_target_sources(self, env):
        reg, _, _ = env
        sid = _call(reg, "add_source", {
            "name": "a", "kind": "m3u", "location": "C:/a.m3u"})["id"]
        out = _call(reg, "set_target_sources",
                    {"target_id": "0", "m3u_ids": [sid]})
        assert out.get("ok")
        t = _call(reg, "get_target", {"target_id": "0"})["target"]
        assert t["source_refs"][0]["id"] == sid

    def test_set_target_sources_target_inexistente(self, env):
        reg, _, _ = env
        out = _call(reg, "set_target_sources",
                    {"target_id": "99", "m3u_ids": []})
        assert "error" in out

    def test_set_target_sources_id_desconocido(self, env):
        reg, _, _ = env
        out = _call(reg, "set_target_sources",
                    {"target_id": "0", "m3u_ids": ["fantasma"]})
        assert "error" in out


def _parse(path: Path):
    from src.infrastructure.client import config_toml

    return config_toml.load(path)
