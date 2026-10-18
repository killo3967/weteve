"""REST v1 servida por el mismo puerto que /mcp (McpHttpTransport)."""

import http.client
import json

import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.infrastructure.web.rest_api import RestApi


class _FakeService:
    def status(self) -> dict:
        return {"targets": {"0": {"name": "test", "running": True}}}

    def list_targets(self) -> dict:
        return {"targets": {"0": {"name": "test"}}}

    def get_target(self, target_id: str):
        return {"target": {"name": "test"}} if target_id == "0" else None

    def runtime_status(self, target_id: str):
        return {"name": "test", "running": True}

    def channels(self, target_id, limit=50, offset=0, unmatched_only=False):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def mapping_candidates(self, target_id, limit=200, offset=0):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def epg_channels(self, target_id, limit=200, offset=0):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def set_rules(self, target_id, rules, reload=True):
        return {"ok": True, "reloaded": False}


def _get(port: int, path: str, token: str = "") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else {}
    finally:
        conn.close()


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body) if body else {}
    finally:
        conn.close()


@pytest.fixture
def transport():
    server = McpHttpTransport(McpStreamableServer(), port=0,
                              rest_api=RestApi(_FakeService()))
    assert server.start()
    yield server
    server.stop()


class TestRestEnElPuertoMcp:
    def test_health_por_http(self, transport):
        code, body = _get(transport.port, "/api/v1/health")
        assert code == 200 and body == {"ok": True, "api": "v1"}

    def test_target_inexistente(self, transport):
        assert _get(transport.port, "/api/v1/targets/9")[0] == 404

    def test_channels(self, transport):
        code, body = _get(transport.port,
                          "/api/v1/targets/0/channels?unmatched_only=true")
        assert code == 200 and body["total"] == 0

    def test_metodo_no_soportado(self, transport):
        import http.client as hc

        conn = hc.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("DELETE", "/api/v1/targets/0/rules")
            assert conn.getresponse().status == 405
        finally:
            conn.close()

    def test_escritura_por_http(self, transport):
        """PUT sobre /api/v1 llega al servicio y responde 200."""
        import http.client as hc

        conn = hc.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("PUT", "/api/v1/targets/0/rules",
                         body=json.dumps({"rules": [], "reload": False}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 200
            assert json.loads(resp.read().decode())["ok"] is True
        finally:
            conn.close()

    def test_mcp_sigue_funcionando(self, transport):
        code, body = _post(transport.port, "/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}})
        assert code == 200 and body["result"]["serverInfo"]["name"] == "weteve"

    def test_ruta_desconocida_fuera_de_la_api(self, transport):
        code, _ = _get(transport.port, "/nada")
        assert code == 405  # GET sin SSE en /mcp sigue rechazándose

    def test_token_obligatorio_en_la_api(self):
        server = McpHttpTransport(McpStreamableServer(), port=0, token="secreto",
                                  rest_api=RestApi(_FakeService()))
        assert server.start()
        try:
            assert _get(server.port, "/api/v1/health")[0] == 401
            assert _get(server.port, "/api/v1/health", token="secreto")[0] == 200
        finally:
            server.stop()


class TestCorsYToken:
    def test_preflight_de_origen_loopback(self, transport):
        conn = http.client.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("OPTIONS", "/api/v1/targets",
                         headers={"Origin": "http://127.0.0.1:5173"})
            resp = conn.getresponse()
            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") == \
                "http://127.0.0.1:5173"
            assert "PUT" in resp.getheader("Access-Control-Allow-Methods")
        finally:
            conn.close()

    def test_origen_externo_no_recibe_cors(self, transport):
        conn = http.client.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("OPTIONS", "/api/v1/targets",
                         headers={"Origin": "http://evil.example"})
            resp = conn.getresponse()
            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") is None
        finally:
            conn.close()

    def test_options_fuera_de_la_api(self, transport):
        conn = http.client.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("OPTIONS", "/mcp")
            assert conn.getresponse().status == 405
        finally:
            conn.close()

    def test_cors_en_la_respuesta_normal(self, transport):
        conn = http.client.HTTPConnection("127.0.0.1", transport.port, timeout=5)
        try:
            conn.request("GET", "/api/v1/health",
                         headers={"Origin": "http://localhost:3000"})
            resp = conn.getresponse()
            resp.read()
            assert resp.getheader("Access-Control-Allow-Origin") == \
                "http://localhost:3000"
        finally:
            conn.close()
