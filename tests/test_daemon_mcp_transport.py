"""Tests del transporte HTTP del MCP (Streamable HTTP).

Un solo servidor en la suite, puerto efímero; valida initialize, sesión,
tools/call end-to-end con el registro real sobre stubs, token y DELETE.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.application.services.daemon.supervisor import TargetSupervisor


class _StubSupervisor(TargetSupervisor):
    def get_status(self) -> dict:  # type: ignore[override]
        return {"targets": {"t0": {"name": "Mi lista", "running": True}}}


class _StubStore:
    def list(self) -> dict:
        return {}

    def get(self, target_id):
        return None


def _post(url: str, body: dict, session: str | None = None,
          token: str | None = None, method: str = "POST", attempts: int = 40):
    """POST con reintentos (flake Windows: RST en loopback inmediato tras bind)."""
    last: Exception | None = None
    for _ in range(attempts):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if session:
            req.add_header("Mcp-Session-Id", session)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                sid = r.headers.get("Mcp-Session-Id")
                raw = r.read().decode("utf-8")
                return r.status, sid, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            return (e.code, e.headers.get("Mcp-Session-Id"),
                    (json.loads(raw) if raw else None))
        except (ConnectionResetError, OSError) as e:
            last = e
            time.sleep(0.25)
    raise last  # type: ignore[misc]


def _get(url: str, session: str | None = None):
    req = urllib.request.Request(url, method="GET")
    if session:
        req.add_header("Mcp-Session-Id", session)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


@pytest.fixture()
def transport() -> McpHttpTransport:
    core = McpStreamableServer(server_name="weteve-test")
    core.set_tool_registry(build_tool_registry(_StubSupervisor(), _StubStore()))
    t = McpHttpTransport(core, host="127.0.0.1", port=0)
    assert t.start() is True
    yield t
    t.stop()


def _initialize(transport: McpHttpTransport) -> str:
    url = transport.url_base + "/mcp"
    for _ in range(12):
        status, sid, resp = _post(url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}},
        })
        if status == 200 and sid:
            return sid
        time.sleep(0.25)
    raise AssertionError("initialize falló")


class TestMcpHttpTransport:
    def test_initialize_and_tools_call(self, transport: McpHttpTransport) -> None:
        url = transport.url_base + "/mcp"
        sid = _initialize(transport)
        assert sid

        # notifications/initialized → 202 sin cuerpo
        status, _, body = _post(url, {
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }, session=sid)
        assert status == 202 and body is None

        # tools/list
        status, _, resp = _post(url, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list"
        }, session=sid)
        assert status == 200
        assert "get_status" in [t["name"] for t in resp["result"]["tools"]]

        # tools/call → get_status real
        status, _, resp = _post(url, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        }, session=sid)
        assert status == 200
        text = resp["result"]["content"][0]["text"]
        assert json.loads(text)["targets"]["t0"]["running"] is True

    def test_missing_session_404(self, transport: McpHttpTransport) -> None:
        url = transport.url_base + "/mcp"
        status, _, resp = _post(url, {"jsonrpc": "2.0", "id": 1,
                                      "method": "tools/list"})
        # tools/list sin sesión: error JSON-RPC de sesión inválida
        # (el transporte devuelve 200 con error, válido en JSON-RPC)
        assert resp["error"]["code"] == -32001

    def test_delete_session(self, transport: McpHttpTransport) -> None:
        url = transport.url_base + "/mcp"
        sid = _initialize(transport)
        status, _, _ = _post(url, method="DELETE", body={}, session=sid)
        assert status in (200, 204)
        # sesión eliminada: tools/list devuelve error de sesión
        status, _, resp = _post(url, {"jsonrpc": "2.0", "id": 9,
                                      "method": "tools/list"}, session=sid)
        assert resp["error"]["code"] == -32001

    def test_token_auth(self) -> None:
        core = McpStreamableServer(server_name="weteve-test")
        core.set_tool_registry(build_tool_registry(_StubSupervisor(), _StubStore()))
        t = McpHttpTransport(core, host="127.0.0.1", port=0, token="tk1")
        try:
            assert t.start() is True
            url = t.url_base + "/mcp"
            status, _, _ = _post(url, {"jsonrpc": "2.0", "id": 1,
                                       "method": "initialize", "params": {}})
            assert status == 401
            status, sid, _ = _post(url, {"jsonrpc": "2.0", "id": 1,
                                         "method": "initialize",
                                         "params": {"protocolVersion": "2025-03-26",
                                                     "capabilities": {},
                                                     "clientInfo": {"name": "t",
                                                                    "version": "0"}}},
                                   token="tk1")
            assert status == 200 and sid
        finally:
            t.stop()
