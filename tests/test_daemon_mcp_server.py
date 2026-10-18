"""Tests del servidor MCP (Streamable HTTP / JSON-RPC 2.0).

Protocolo testeado directamente sobre ``handle_request`` (determinista); un
único test de humo HTTP valida transporte y cabeceras de sesión.
"""


import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.supervisor import TargetSupervisor


class _StubSupervisor(TargetSupervisor):
    """Devuelve datos fijos; suficiente para testear el protocolo MCP."""

    def get_status(self) -> dict:  # type: ignore[override]
        return {"targets": {"t0": {"name": "Mi lista", "running": True,
                                    "channel_count": 5}}}

    def list_definitions(self) -> dict:  # type: ignore[override]
        return {"t0": {"name": "Mi lista", "port": 8080}}


@pytest.fixture()
def server() -> McpStreamableServer:
    srv = McpStreamableServer(server_name="weteve-test")
    srv.set_tool_registry(build_tool_registry(_StubSupervisor(), target_store=None))
    return srv


def _init(server: McpStreamableServer) -> dict:
    resp, session = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {
             "protocolVersion": "2025-03-26",
             "capabilities": {},
             "clientInfo": {"name": "test", "version": "0"},
         }},
        session_id=None,
    )
    assert session is not None
    return {"session": session, "resp": resp}


class TestInitialize:
    def test_initialize_returns_capabilities(self, server) -> None:
        result = _init(server)["resp"]
        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 1
        body = result["result"]
        assert body["protocolVersion"] == "2025-03-26"
        assert "tools" in body["capabilities"]
        assert body["serverInfo"]["name"] == "weteve-test"

    def test_unknown_session_rejected(self, server) -> None:
        resp, session = server.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id="bogus",
        )
        assert session is None
        assert resp["error"]["code"] == -32001  # sesión inválida

    def test_tools_list_after_initialize(self, server) -> None:
        s = _init(server)["session"]
        resp, _ = server.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session_id=s
        )
        names = [t["name"] for t in resp["result"]["tools"]]
        # superficie completa acordada: estado + CRUD + ciclo de vida + pipeline
        assert set(names) == {
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
        # cada herramienta declara inputSchema
        for t in resp["result"]["tools"]:
            assert "inputSchema" in t

    def test_unknown_tool(self, server) -> None:
        s = _init(server)["session"]
        resp, _ = server.handle_request(
            {"jsonrpc": "2.0", "id": 4,
             "method": "tools/call",
             "params": {"name": "nope", "arguments": {}}},
            session_id=s,
        )
        assert resp["error"]["code"] == -32602

    def test_ping(self, server) -> None:
        s = _init(server)["session"]
        resp, _ = server.handle_request(
            {"jsonrpc": "2.0", "id": 5, "method": "ping"}, session_id=s
        )
        assert resp["result"] == {}

    def test_delete_session(self, server) -> None:
        s = _init(server)["session"]
        server.handle_delete(s)
        resp, _ = server.handle_request(
            {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}, session_id=s
        )
        assert resp["error"]["code"] == -32001

    def test_notification_no_response(self, server) -> None:
        """notifications/initialized: sin id → respuesta None (204 en HTTP)."""
        s = _init(server)["session"]
        resp, session = server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=s,
        )
        assert resp is None
        assert session == s
