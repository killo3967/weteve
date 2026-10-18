"""E2E del endpoint MCP contra un daemon real.

Cubre el protocolo (initialize, sesión, notificación, cierre, token) y la
paridad con REST: lo que escribe un agente se ve en la API y en lo servido.
El ciclo completo de las 23 herramientas vive en ``test_daemon_e2e_tools.py``.
"""

import http.client
import json

from e2e_support import EXPECTED_TOOLS, start


class TestProtocolo:
    def test_tools_list_completa(self, daemon):
        mcp = daemon.mcp()
        nombres = set(mcp.tools())
        assert nombres == EXPECTED_TOOLS
        assert len(nombres) == 23

    def test_notificacion_aceptada(self, daemon):
        mcp = daemon.mcp()
        status, _, _ = mcp.notify("notifications/initialized")
        assert status == 202

    def test_sesion_invalida(self, daemon):
        mcp = daemon.mcp()
        sesion = mcp.session
        mcp.session = None
        try:
            _, _, resp = mcp.raw({"jsonrpc": "2.0", "id": 2,
                                  "method": "tools/list"})
            assert resp["error"]["code"] == -32001
        finally:
            mcp.session = sesion

    def test_cierre_de_sesion(self, daemon):
        mcp = daemon.mcp()
        assert mcp.delete_session() == 204
        _, _, resp = mcp.raw({"jsonrpc": "2.0", "id": 3,
                              "method": "tools/list"})
        assert resp["error"]["code"] == -32001

    def test_token_obligatorio(self, tmp_path):
        (tmp_path / "tok").mkdir()
        handle = start(tmp_path / "tok", token="tk")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", handle.api_port,
                                              timeout=10)
            try:
                conn.request("POST", "/mcp", body=json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {}}),
                    headers={"Content-Type": "application/json"})
                assert conn.getresponse().status == 401
            finally:
                conn.close()
            # con token, el cliente normal funciona
            assert "get_status" in handle.mcp().tools()
        finally:
            handle.stop()


class TestParidadConRest:
    def test_mapeo_mcp_visible_en_rest(self, daemon, api):
        mcp = daemon.mcp()
        out = mcp.call("modify_mappings", {"target_id": "0", "mappings": [
            {"channel_url": "http://stream.example.com/cineplus",
             "epg_id": "es.la1"}]})
        assert out["ok"] is True and out["errors"] == []

        target = api.get("/targets/0")[1]["target"]
        assert target["epg_overrides"] == {
            "http://stream.example.com/cineplus": "es.la1"}

        assert api.run("0")[1]["filtered_count"] == 4
        assert "Canal Cine Plus" in daemon.fetch("/playlist.m3u")[1]

    def test_filtro_mcp_visible_en_rest(self, daemon, api):
        mcp = daemon.mcp()
        out = mcp.call("create_filter", {"target_id": "0",
                                         "field": "group-title",
                                         "pattern": "Cine",
                                         "exclude": True, "operator": "and"})
        assert out["ok"] is True
        reglas = api.get("/targets/0")[1]["target"]["rules"]
        assert "group-title:!{Cine}:0:and" in reglas

        assert api.run("0")[1]["filtered_count"] == 3
        assert "Canal Cine Plus" not in daemon.fetch("/playlist.m3u")[1]
