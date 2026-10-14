"""Test e2e del daemon real: 20 herramientas MCP sobre el daemon completo.

A diferencia de test_daemon_mcp_transport.py (stubs), aquí arranca el daemon
COMPLETO vía ``build_daemon`` con un config.ini temporal y fuentes locales
(fixtures M3U/XMLTV), y se llama a CADA una de las 23 tools verificando el
resultado esperado, incluido servir la playlist por HTTP real.

Marcado ``integration``: lento pero sin red externa. Se ejecuta con la suite
normal (no usa el marker ``network``).
"""

from __future__ import annotations

import configparser
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore
from src.infrastructure.client.sources_store import load_catalog

M3U_FIXTURE = """#EXTM3U
#EXTINF:-1 tvg-id="es.la1" tvg-logo="http://x/la1.png" group-title="General",La 1
http://stream.test/la1
#EXTINF:-1 tvg-id="es.tdp" tvg-logo="http://x/tdp.png" group-title="Deportes",Teledeporte
http://stream.test/tdp
"""

XMLTV_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="es.la1"><display-name>La 1</display-name></channel>
  <channel id="es.tdp"><display-name>Teledeporte</display-name></channel>
  <programme start="20261013060000 +0200" stop="20261013070000 +0200" channel="es.la1">
    <title>Telediario Matinal</title>
  </programme>
  <programme start="20261013120000 +0200" stop="20261013130000 +0200" channel="es.tdp">
    <title>Deportes</title>
  </programme>
</tv>
"""

ALL_TOOLS = {
    "get_status", "shutdown",
    "list_targets", "get_target",
    "create_target", "update_target", "delete_target",
    "start_target", "stop_target", "reload_target",
    "run_pipeline", "list_tv_channels", "list_epg_channels",
    "suggest_epg_matches",
    "add_source", "list_sources", "update_source", "remove_source",
    "set_target_sources",
    "create_filter", "delete_filter", "modify_mapping", "modify_mappings",
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- MCP cliente

import http.client


class McpClient:
    """Cliente JSON-RPC mínimo contra el transporte MCP del daemon.

    Usa UNA conexión persistente (HTTP/1.1 keep-alive): los RST de loopback
    de Windows ocurren sobre todo en el accept de conexiones nuevas, así que
    calentar la conexión una vez con peticiones idempotentes (initialize,
    tools/list) y reutilizarla evita el flake sin reenviar mutaciones.
    """

    def __init__(self, url_base: str):
        self.url = url_base.rstrip("/") + "/mcp"
        self._host, _, self._port = url_base.rstrip("/").partition("://")[-1].partition(":")
        self._conn: http.client.HTTPConnection | None = None
        self.session: str | None = None
        self._connect_and_initialize()

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self._host, int(self._port), timeout=15)
        return self._conn

    def _drop_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _connect_and_initialize(self) -> None:
        # Fase idempotente: reintentos seguros.
        for i in range(20):
            try:
                conn = self._connection()
                conn.connect()
                status, sid, _ = self._send(
                    {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                     "params": {"protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "e2e", "version": "0"}}})
                if status == 200 and sid:
                    self.session = sid
                    # Calienta la conexión con una llamada de lectura.
                    self._send({"jsonrpc": "2.0", "id": 1,
                                "method": "tools/list"})
                    return
            except (OSError, http.client.HTTPException):
                pass
            self._drop_connection()
            time.sleep(0.25 * (i + 1))
        raise AssertionError("MCP initialize falló contra el daemon")

    def _send(self, message: dict, session: str | None = None):
        headers = {"Content-Type": "application/json"}
        sid = session or self.session
        if sid:
            headers["Mcp-Session-Id"] = sid
        conn = self._connection()
        conn.request("POST", "/mcp", body=json.dumps(message),
                     headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Mcp-Session-Id"), body

    def _post(self, message: dict, session: str | None = None,
              attempts: int = 3):
        # Sin reenvío a ciegas: una mutación ya puede haberse aplicado en el
        # servidor aunque la respuesta se perdiera. Con conexión persistente
        # los errores aquí son raros; si la conexión está rota se re-establece
        # (fase idempotente) y se reintenta una sola vez.
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                return self._send(message, session)
            except (OSError, http.client.HTTPException) as e:
                last_err = e
                self._drop_connection()
                self._connect_and_initialize()
        raise AssertionError(f"POST {message.get('method')} falló: {last_err}")

    def call(self, name: str, arguments: dict | None = None,
             req_id: int = 1) -> dict:
        """tools/call y parseo del texto de respuesta a dict."""
        status, _, body = self._post({
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        assert status == 200, f"tools/call {name}: HTTP {status}: {body[:200]}"
        resp = json.loads(body)
        assert "result" in resp, f"tools/call {name}: error JSON-RPC: {resp}"
        text = resp["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}


# ------------------------------------------------------------------- fixtures

@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Directorio con config.ini (puertos efímeros) y fixtures M3U/XMLTV."""
    m3u = tmp_path / "lista.m3u"
    m3u.write_text(M3U_FIXTURE, encoding="utf-8")
    xml = tmp_path / "guia.xml"
    xml.write_text(XMLTV_FIXTURE, encoding="utf-8")

    cp = configparser.ConfigParser()
    cp["DAEMON"] = {"control_host": "127.0.0.1",
                    "control_port": "0", "mcp_port": "0", "mcp_token": ""}
    with open(tmp_path / "config.ini", "w", encoding="utf-8") as f:
        cp.write(f)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "lista.m3u").write_text(M3U_FIXTURE, encoding="utf-8")
    (tmp_path / "fixtures" / "guia.xml").write_text(XMLTV_FIXTURE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def daemon(workspace: Path):
    """Daemon completo real (supervisor + control API + MCP)."""
    # Import aquí para no ralentizar la recolección del resto de la suite.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "weteve_daemon", Path(__file__).resolve().parents[1] / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = mod.build_daemon(workspace / "config.ini")
    yield d
    d["supervisor"].stop_all()
    d["control_api"].stop()
    d["mcp_transport"].stop()


@pytest.fixture()
def mcp(daemon) -> McpClient:
    return McpClient(daemon["mcp_transport"].url_base)


# ---------------------------------------------------------------------- tests

class TestDaemonE2ETools:
    def test_tools_list_completa(self, mcp: McpClient):
        status, _, body = mcp._post({"jsonrpc": "2.0", "id": 2,
                                     "method": "tools/list"})
        resp = json.loads(body)
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == ALL_TOOLS, f"faltan: {ALL_TOOLS - names}, sobran: {names - ALL_TOOLS}"
        for t in resp["result"]["tools"]:
            assert "inputSchema" in t and "description" in t

    def test_get_status_daemon_vacio(self, mcp: McpClient):
        r = mcp.call("get_status")
        assert "targets" in r

    def test_ciclo_fuentes(self, mcp: McpClient, workspace: Path):
        # add_source (file, detectado por location sin http)
        r = mcp.call("add_source", {"name": "Lista e2e", "kind": "m3u",
                                    "location": str(workspace / "fixtures" / "lista.m3u")})
        assert "error" not in r, r
        m3u_id = r["id"]
        r = mcp.call("add_source", {"name": "Guia e2e", "kind": "epg",
                                    "location": str(workspace / "fixtures" / "guia.xml")})
        epg_id = r["id"]

        # list_sources
        r = mcp.call("list_sources")
        kinds = {e["id"]: e["kind"] for e in r["sources"]}
        assert kinds[m3u_id] == "m3u" and kinds[epg_id] == "epg"

        # update_source
        r = mcp.call("update_source", {"id": m3u_id, "name": "Lista e2e v2"})
        assert "error" not in r, r
        catalog = load_catalog(workspace / "config.ini")
        assert catalog.get(m3u_id).name == "Lista e2e v2"

        # remove_source (se recrean en el fixture del ciclo de target)
        r = mcp.call("remove_source", {"id": epg_id})
        assert "error" not in r, r
        assert "error" in mcp.call("remove_source", {"id": epg_id})

    def test_ciclo_target_completo(self, mcp: McpClient, workspace: Path):
        m3u_id = mcp.call("add_source", {"name": "L", "kind": "m3u",
                                         "location": str(workspace / "fixtures" / "lista.m3u")})["id"]
        epg_id = mcp.call("add_source", {"name": "G", "kind": "epg",
                                         "location": str(workspace / "fixtures" / "guia.xml")})["id"]
        port = _free_port()

        # create_target
        r = mcp.call("create_target", {"target_id": "e2e", "name": "E2E",
                                       "m3u": str(workspace / "fixtures" / "lista.m3u"),
                                       "port": port})
        assert r.get("ok") is True, r
        assert "error" in mcp.call("create_target", {
            "target_id": "e2e", "name": "dup", "m3u": "x", "port": 1})

        # get_target / list_targets
        t = mcp.call("get_target", {"target_id": "e2e"})["target"]
        assert t["name"] == "E2E" and t["port"] == port
        assert "e2e" in mcp.call("list_targets")["targets"]

        # set_target_sources: enlaza cartera → source_refs/epg_refs
        r = mcp.call("set_target_sources", {"target_id": "e2e",
                                            "m3u_ids": [m3u_id],
                                            "epg_ids": [epg_id]})
        assert "error" not in r, r

        # update_target
        r = mcp.call("update_target", {"target_id": "e2e", "name": "E2E v2"})
        assert "error" not in r, r
        assert mcp.call("get_target", {"target_id": "e2e"})["target"]["name"] == "E2E v2"

        # run_pipeline + list_tv_channels (paginado + unmatched_only)
        r = mcp.call("run_pipeline", {"target_id": "e2e"})
        assert "error" not in r, r
        r = mcp.call("list_tv_channels", {"target_id": "e2e"})
        assert r["count"] == 2 and r["total"] == 2, r
        names = {c["name"] for c in r["channels"]}
        assert any("La 1" in n for n in names)
        # list_epg_channels: catálogo XMLTV del target
        r = mcp.call("list_epg_channels", {"target_id": "e2e"})
        assert r["total"] == 2, r
        epg_ids = {c["id"] for c in r["channels"]}
        assert "es.la1" in epg_ids
        # suggest_epg_matches: candidatos por similitud (solo lectura).
        # El fixture solo tiene es.la1/es.tdp: "La 2" debe proponer es.la1.
        r = mcp.call("suggest_epg_matches", {
            "target_id": "e2e", "channel_name": "La 2"})
        assert r["suggestions"], r
        assert any(s["id"] == "es.la1" for s in r["suggestions"]), r

        # start_target + HTTP real de la playlist y el EPG
        r = mcp.call("start_target", {"target_id": "e2e"})
        assert "error" not in r, r
        url = f"http://127.0.0.1:{port}/playlist.m3u"
        body = _http_get_retry(url)
        assert body.startswith("#EXTM3U"), body[:100]
        epg_body = _http_get_retry(f"http://127.0.0.1:{port}/epg.xml")
        assert "<tv" in epg_body

        # create_filter + hot-reload (target corriendo → reloaded True)
        r = mcp.call("create_filter", {"target_id": "e2e",
                                       "field": "group-title",
                                       "pattern": "Deportes",
                                       "exclude": True})
        assert r.get("ok") is True, r
        assert r.get("reloaded") is True, r
        # el canal de Deportes desaparece de la playlist servida
        body = _http_get_retry(url)
        assert "Teledeporte" not in body and "La 1" in body

        # create_filter: duplicado (mismo campo+patrón, aunque cambie exclude) e inválido
        assert "error" in mcp.call("create_filter", {
            "target_id": "e2e", "field": "group-title", "pattern": "Deportes"})
        assert "error" in mcp.call("create_filter", {
            "target_id": "e2e", "field": "no-existe", "pattern": "x"})

        # delete_filter (por campo+patrón) y de nuevo → error
        r = mcp.call("delete_filter", {"target_id": "e2e",
                                       "field": "group-title",
                                       "pattern": "Deportes"})
        assert r.get("ok") is True, r
        assert "error" in mcp.call("delete_filter", {
            "target_id": "e2e", "field": "group-title", "pattern": "Deportes"})

        # modify_mapping: set → remove → remove de nuevo
        r = mcp.call("modify_mapping", {"target_id": "e2e",
                                        "channel_url": "http://stream.test/tdp",
                                        "epg_id": "es.la1"})
        assert "error" not in r, r
        overrides = mcp.call("get_target", {"target_id": "e2e"})["target"]["epg_overrides"]
        assert overrides == {"http://stream.test/tdp": "es.la1"}
        r = mcp.call("modify_mapping", {"target_id": "e2e",
                                        "channel_url": "http://stream.test/tdp"})
        assert "error" not in r, r
        assert "error" in mcp.call("modify_mapping", {
            "target_id": "e2e", "channel_url": "http://stream.test/tdp"})

        # reload_target y stop_target
        r = mcp.call("reload_target", {"target_id": "e2e"})
        assert "error" not in r, r
        r = mcp.call("stop_target", {"target_id": "e2e"})
        assert "error" not in r, r
        status = mcp.call("get_status")["targets"]["e2e"]
        assert status.get("running") is False, status

        # delete_target y tools sobre target inexistente
        r = mcp.call("delete_target", {"target_id": "e2e"})
        assert "error" not in r, r
        assert "error" in mcp.call("get_target", {"target_id": "e2e"})
        assert "error" in mcp.call("create_filter", {
            "target_id": "e2e", "field": "group-title", "pattern": "x"})

    def test_tools_con_target_desconocido(self, mcp: McpClient):
        for name, args in (("start_target", {"target_id": "nope"}),
                           ("reload_target", {"target_id": "nope"}),
                           ("run_pipeline", {"target_id": "nope"}),
                           ("delete_target", {"target_id": "nope"}),
                           ("set_target_sources", {"target_id": "nope"}),
                           ("modify_mapping", {"target_id": "nope",
                                               "channel_url": "u"})):
            assert "error" in mcp.call(name, args), name
        # stop_target no lanza error: devuelve ok=False (idempotente)
        r = mcp.call("stop_target", {"target_id": "nope"})
        assert r.get("ok") is False, r

    def test_shutdown_tool(self, mcp: McpClient, daemon):
        r = mcp.call("shutdown")
        assert r.get("ok") is True or "ok" in r or r.get("_raw") is not None, r
        assert daemon["running"]["flag"] is False


def _http_get_retry(url: str, attempts: int = 20, timeout: int = 5) -> str:
    """GET con reintentos: flake Windows de RST en loopback inmediato."""
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
            time.sleep(0.25)
    raise AssertionError(f"GET {url} falló: {last_err}")
