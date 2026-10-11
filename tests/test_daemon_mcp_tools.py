"""Tests del registro de herramientas MCP (mcp_tools)."""

import pytest

from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import FilterRule, PipelineTarget


class _StubSupervisor(TargetSupervisor):
    def get_status(self) -> dict:  # type: ignore[override]
        return {"targets": {"t0": {"name": "Mi lista", "running": True,
                                   "channel_count": 5, "port": 8080,
                                   "playlist_url": "http://x/playlist.m3u"}}}

    def run_pipeline(self, target_id: str) -> dict:  # type: ignore[override]
        return {"total_input": 10, "filtered_count": 5, "unmatched_count": 0}

    def preview_channels(self, target_id: str, limit: int = 20) -> list[dict]:  # type: ignore[override]
        return [{"name": "Canal A", "url": "http://s/1", "group": "G",
                 "tvg_id": None}][:limit]

    def reload_target(self, target_id, target=None, epg_path=None) -> bool:  # type: ignore[override]
        return True

    def stop_target(self, target_id: str) -> bool:  # type: ignore[override]
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
            "run_pipeline", "preview_channels",
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
        preview = registry["preview_channels"]["handler"](target_id="t0", limit=1)
        assert preview["channels"][0]["name"] == "Canal A"

    def test_rule_parsing_from_mcp_args(self, registry: dict) -> None:
        """Las reglas llegan en sintaxis xteve textual y se parsean."""
        result = registry["create_target"]["handler"](
            target_id="t2", name="R", m3u="/r.m3u", port=8082,
            rules=["group-title:!{Adultos}:0"],
        )
        assert result["ok"] is True
