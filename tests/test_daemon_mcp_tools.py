"""Tests del registro de herramientas MCP (mcp_tools)."""

import pytest

from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import PipelineTarget


class _StubSupervisor(TargetSupervisor):
    def get_status(self) -> dict:
        return {"targets": {"t0": {"name": "Mi lista", "running": True,
                                   "channel_count": 5, "port": 8080,
                                   "playlist_url": "http://x/playlist.m3u"}}}

    def run_pipeline(self, target_id: str) -> dict:
        return {"total_input": 10, "filtered_count": 5, "unmatched_count": 0}

    def metrics(self, target_id: str) -> dict:
        return {"total_input": 10, "filtered_count": 5, "unmatched_count": 0}

    def has_snapshot(self, target_id: str) -> bool:
        return True

    def list_tv_channels(self, target_id: str, limit: int = 50,
                         offset: int = 0, unmatched_only: bool = False
                         ) -> dict:
        rows = [{"name": "Canal A", "url": "http://s/1", "group": "G",
                 "tvg_id": None}]
        if unmatched_only:
            rows = [r for r in rows if not r["tvg_id"]]
        return {"channels": rows, "total": len(rows),
                "count": len(rows), "offset": offset}

    def list_epg_channels(self, target_id: str, limit: int = 200,
                          offset: int = 0) -> dict:
        return {"channels": [{"id": "es.la1", "display_name": "La 1"}],
                "total": 1, "count": 1, "offset": 0}

    def suggest_epg_matches(self, target_id: str, channel_name: str,
                            limit: int = 5) -> dict:
        return {"suggestions": [{"id": "es.la1", "display_name": "La 1"}],
                "channel_name": channel_name}

    def reload_target(self, target_id, target=None, epg_path=None) -> bool:
        return True

    def stop_target(self, target_id: str) -> bool:
        return target_id == "t0"


class _StubStore:
    """TargetStore mínimo: diccionario en memoria (misma firma que el real)."""

    def __init__(self) -> None:
        self.targets: dict[str, PipelineTarget] = {
            "t0": PipelineTarget(name="Mi lista", source_index="/x.m3u", port=8080),
        }
        self.saved: dict = {}
        self.epg_paths: dict[str, str | None] = {"t0": None}

    def list(self) -> dict:
        return dict(self.targets)

    def get(self, target_id: str):
        return self.targets.get(target_id)

    def get_epg_path(self, target_id: str) -> str | None:
        return self.epg_paths.get(target_id)

    def save(self, target_id: str, target: PipelineTarget,
             epg_path: str | None = None, bind: str | None = None) -> None:
        self.targets[target_id] = target
        self.epg_paths[target_id] = epg_path
        self.saved[target_id] = True

    def delete(self, target_id: str) -> bool:
        self.epg_paths.pop(target_id, None)
        return self.targets.pop(target_id, None) is not None


@pytest.fixture()
def registry() -> dict:
    return build_tool_registry(_StubSupervisor(), target_store=_StubStore())


class TestRegistry:
    def test_full_surface(self, registry: dict) -> None:
        expected = {
            "get_status", "shutdown",
            "list_targets", "get_target",
            "create_target", "update_target", "delete_target",
            "start_target", "stop_target", "reload_target",
            "run_pipeline", "list_tv_channels", "list_epg_channels",
            "suggest_epg_matches",
            "add_source", "list_sources", "update_source", "remove_source",
            "set_target_sources",
            "create_filter", "delete_filter",
            "modify_mapping", "modify_mappings",
        }
        assert set(registry) == expected
        for spec in registry.values():
            assert "description" in spec and "schema" in spec and "handler" in spec

    def test_get_status_handler(self, registry: dict) -> None:
        result = registry["get_status"]["handler"]()
        assert result["targets"]["t0"]["running"] is True

    def test_get_status_with_shutdown_callback(self) -> None:
        called = []
        reg = build_tool_registry(_StubSupervisor(), target_store=None,
                                  on_shutdown=lambda: called.append(1))
        reg["shutdown"]["handler"]()
        assert called == [1]

    def test_list_targets(self, registry: dict) -> None:
        result = registry["list_targets"]["handler"]()
        assert result["targets"]["t0"]["name"] == "Mi lista"

    def test_get_target_unknown(self, registry: dict) -> None:
        result = registry["get_target"]["handler"](target_id="ghost")
        assert "error" in result

    def test_get_target_ok(self, registry: dict) -> None:
        result = registry["get_target"]["handler"](target_id="t0")
        assert result["target"]["name"] == "Mi lista"
        # las reglas se serializan a sintaxis xteve para lectura por agentes
        assert "rules" in result["target"]

    def test_create_and_delete_target(self, registry: dict) -> None:
        args = {
            "target_id": "t1",
            "name": "Nueva",
            "m3u": "/tmp/nueva.m3u",
            "port": 8081,
            "rules": ["group-title:{Deportes}:0"],
        }
        result = registry["create_target"]["handler"](**args)
        assert result["ok"] is True
        result2 = registry["delete_target"]["handler"](target_id="t1")
        assert result2["ok"] is True

    def test_create_requires_store(self, registry: dict) -> None:
        """create/update/delete sin store: error claro."""
        reg = build_tool_registry(_StubSupervisor(), target_store=None)
        result = reg["create_target"]["handler"](
            target_id="t9", name="x", m3u="/x.m3u", port=8081
        )
        assert "error" in result

    def test_update_target(self, registry: dict) -> None:
        result = registry["update_target"]["handler"](
            target_id="t0", name="Renombrada"
        )
        assert result["ok"] is True

    def test_update_unknown(self, registry: dict) -> None:
        result = registry["update_target"]["handler"](target_id="ghost", name="x")
        assert "error" in result

    def test_lifecycle_handlers(self, registry: dict) -> None:
        assert registry["reload_target"]["handler"](target_id="t0")["ok"] is True
        assert registry["stop_target"]["handler"](target_id="t0")["ok"] is True
        assert registry["stop_target"]["handler"](target_id="ghost")["ok"] is False

    def test_pipeline_handlers(self, registry: dict) -> None:
        metrics = registry["run_pipeline"]["handler"](target_id="t0")
        assert metrics["filtered_count"] == 5
        page = registry["list_tv_channels"]["handler"](target_id="t0", limit=1)
        assert page["channels"][0]["name"] == "Canal A"
        assert page["total"] == 1

    def test_rule_parsing_from_mcp_args(self, registry: dict) -> None:
        """Las reglas llegan en sintaxis xteve textual y se parsean."""
        result = registry["create_target"]["handler"](
            target_id="t2", name="R", m3u="/r.m3u", port=8082,
            rules=["group-title:!{Adultos}:0"],
        )
        assert result["ok"] is True


# -- create_filter / delete_filter / modify_mapping ---------------------------

class TestNewFilterTools:
    """create_filter, delete_filter y modify_mapping (con hot-reload)."""

    @pytest.fixture
    def registry(self) -> dict:
        return build_tool_registry(_StubSupervisor(), _StubStore())

    def test_registry_contains_new_tools(self, registry: dict) -> None:
        for name in ("create_filter", "delete_filter", "modify_mapping"):
            assert name in registry
            assert registry[name]["schema"]["required"]

    def test_create_filter_ok_and_hot_reload(self, registry: dict) -> None:
        result = registry["create_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Deportes")
        assert result["ok"] is True
        assert "group-title:Deportes:0" in result["rules"]
        # el target t0 está "running" en el stub: hot-reload aplicado
        assert result["reloaded"] is True

    def test_create_filter_duplicate(self, registry: dict) -> None:
        reg = build_tool_registry(_StubSupervisor(), _StubStore())
        reg["create_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Cine")
        result = reg["create_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Cine")
        assert "error" in result

    def test_create_filter_invalid_field(self, registry: dict) -> None:
        result = registry["create_filter"]["handler"](
            target_id="t0", field="no-existe", pattern="x")
        assert "error" in result

    def test_create_filter_unknown_target(self, registry: dict) -> None:
        result = registry["create_filter"]["handler"](
            target_id="ghost", field="group-title", pattern="x")
        assert "error" in result

    def test_delete_filter_by_field_and_pattern(self, registry: dict) -> None:
        reg = build_tool_registry(_StubSupervisor(), _StubStore())
        reg["create_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Cine")
        result = reg["delete_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Cine")
        assert result["ok"] is True
        assert "group-title:Cine:0" in result["removed"]
        assert "group-title:Cine:0" not in result["rules"]

    def test_delete_filter_not_found(self, registry: dict) -> None:
        result = registry["delete_filter"]["handler"](
            target_id="t0", field="group-title", pattern="Fantasma")
        assert "error" in result

    def test_modify_mapping_set_and_remove(self, registry: dict) -> None:
        reg = build_tool_registry(_StubSupervisor(), _StubStore())
        result = reg["modify_mapping"]["handler"](
            target_id="t0", channel_url="http://s/1", epg_id="epg.canal1")
        assert result["ok"] is True
        assert result["epg_overrides"]["http://s/1"] == "epg.canal1"
        # eliminar: epg_id omitido
        result2 = reg["modify_mapping"]["handler"](
            target_id="t0", channel_url="http://s/1")
        assert result2["ok"] is True
        assert "http://s/1" not in result2["epg_overrides"]

    def test_modify_mapping_remove_missing(self, registry: dict) -> None:
        result = registry["modify_mapping"]["handler"](
            target_id="t0", channel_url="http://sin-mapeo")
        assert "error" in result

    def test_new_tools_require_store(self) -> None:
        reg = build_tool_registry(_StubSupervisor(), target_store=None)
        for name, kwargs in (
            ("create_filter", {"target_id": "t0", "field": "group-title",
                               "pattern": "x"}),
            ("delete_filter", {"target_id": "t0", "field": "group-title",
                               "pattern": "x"}),
            ("modify_mapping", {"target_id": "t0", "channel_url": "u"}),
        ):
            assert "error" in reg[name]["handler"](**kwargs)

    def test_serialize_target_includes_disabled(self, registry: dict) -> None:
        assert "disabled_channels" in registry["get_target"]["handler"](
            target_id="t0")["target"]
