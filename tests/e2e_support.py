"""Soporte de los tests e2e contra el daemon real.

Levanta un daemon completo (``build_daemon``) sobre un ``config.ini`` temporal y
las fixtures M3U/XMLTV del repo, con puertos efímeros y sin red externa. Los
tres ficheros e2e (``test_daemon_e2e_api.py``, ``test_daemon_e2e_playlist.py`` y
``test_daemon_e2e_mcp.py``) usan estas piezas, así que un cambio de arranque se
hace en un solo sitio.

Uso típico en un test::

    from e2e_support import daemon, api          # fixtures

    def test_algo(daemon, api):
        code, body = api.status()
"""

from __future__ import annotations

import configparser
import http.client
import importlib.util
import json
import time
from pathlib import Path

from src.infrastructure.client.api_client import ApiClient

FIXTURES = Path(__file__).parent / "fixtures" / "list_manager"
M3U = FIXTURES / "sample.m3u"       # 5 canales; 2 con tvg-id
XMLTV = FIXTURES / "sample.xmltv"   # 4 canales con programación
TARGET = "0"

# Superficie MCP completa: si se añade una herramienta, este set lo delata.
EXPECTED_TOOLS = {
    "get_status", "shutdown", "list_targets", "get_target",
    "create_target", "update_target", "delete_target",
    "start_target", "stop_target", "reload_target",
    "run_pipeline", "list_tv_channels", "list_epg_channels",
    "suggest_epg_matches",
    "add_source", "list_sources", "update_source", "remove_source",
    "set_target_sources",
    "create_filter", "delete_filter", "modify_mapping", "modify_mappings",
}


# -- arranque ----------------------------------------------------------------

def _daemon_module():
    """Carga ``daemon.py`` sin añadirlo al path global de los tests."""
    spec = importlib.util.spec_from_file_location(
        "weteve_daemon_e2e", Path(__file__).resolve().parents[1] / "daemon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(path: Path, token: str = "") -> Path:
    parser = configparser.ConfigParser()
    parser["DAEMON"] = {
        "control_host": "127.0.0.1", "control_port": "0", "mcp_port": "0",
        "mcp_token": token, "autostart": "0", "stop_on_exit": "0",
    }
    parser[f"listmanager.target.{TARGET}"] = {
        "name": "e2e",
        "custom_m3u": str(M3U).replace("\\", "/"),
        "custom_epg": str(XMLTV).replace("\\", "/"),
        "port": "0",
        "bind": "127.0.0.1",
        "rules": "",
        "epg_overrides": "",
        "disabled_channels": "",
        "m3u_sources": "",
        "epg_sources": "",
    }
    path.write_text("", encoding="utf-8")
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)
    return path


class DaemonHandle:
    """Daemon real con su target sirviendo; encapsula puertos y HTTP."""

    def __init__(self, data: dict, config_path: Path, token: str = "") -> None:
        self._data = data
        self.config_path = config_path
        self.token = token

    # -- puertos -------------------------------------------------------------

    @property
    def api_port(self) -> int:
        """Puerto único: REST, MCP, control y playlists."""
        return self._data["server"].port

    @property
    def control_port(self) -> int:
        return self.api_port

    @property
    def service(self):
        return self._data["service"]

    # -- clientes ------------------------------------------------------------

    def api(self) -> ApiClient:
        return ApiClient(port=self.api_port, token=self.token)

    def status(self) -> dict:
        return self.service.status()["targets"][TARGET]

    def playlist_url(self) -> str:
        return self.status()["playlist_url"]

    def fetch(self, path: str, attempts: int = 5) -> tuple[int, str]:
        """GET al servidor de playlist del target (con reintentos de loopback)."""
        url = self.playlist_url()
        host_port, _, _ = url.partition("://")[2].partition("/")
        host, _, port = host_port.partition(":")
        last: Exception | None = None
        for _ in range(attempts):
            conn = http.client.HTTPConnection(host, int(port), timeout=10)
            try:
                conn.request("GET", path)
                resp = conn.getresponse()
                return resp.status, resp.read().decode("utf-8", "replace")
            except (ConnectionError, OSError) as e:  # RST de loopback
                last = e
                time.sleep(0.05)
            finally:
                conn.close()
        raise last  # type: ignore[misc]

    # -- MCP -----------------------------------------------------------------

    def mcp(self) -> McpClient:
        return McpClient(self.api_port, token=self.token)

    def stop(self) -> None:
        self._data["supervisor"].stop_all()
        self._data["server"].stop()


def start(tmp_path: Path, token: str = "", start_service: bool = True
          ) -> DaemonHandle:
    """Arranca daemon + target (pipeline y servidor de playlist)."""
    config_path = _write_config(tmp_path / "config.ini", token=token)
    data = _daemon_module().build_daemon(config_path)
    if start_service:
        out = data["service"].start_service(TARGET)
        if not out.get("ok"):
            data["supervisor"].stop_all()
            data["server"].stop()
            raise AssertionError(f"el target no arrancó: {out}")
    return DaemonHandle(data, config_path, token=token)


class McpClient:
    """Cliente JSON-RPC del endpoint MCP (una conexión por petición)."""

    def __init__(self, port: int, token: str = "") -> None:
        self.port = port
        self.token = token
        self.session: str | None = None
        self.initialize()

    def _request(self, payload: dict, attempts: int = 5
                 ) -> tuple[int, str | None, dict]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last: Exception | None = None
        for _ in range(attempts):
            conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                              timeout=15)
            try:
                conn.request("POST", "/mcp", body=body, headers=headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8")
                return (resp.status, resp.getheader("Mcp-Session-Id"),
                        json.loads(raw) if raw else {})
            except (ConnectionError, OSError) as e:
                last = e
                time.sleep(0.05)
            finally:
                conn.close()
        raise last  # type: ignore[misc]

    def initialize(self) -> str:
        status, sid, _ = self._request({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "e2e", "version": "0"}}})
        assert status == 200 and sid, "initialize falló"
        self.session = sid
        return sid

    def notify(self, method: str) -> tuple[int, str | None, dict]:
        return self._request({"jsonrpc": "2.0", "method": method})

    def raw(self, payload: dict) -> tuple[int, str | None, dict]:
        """Petición sin sesión asignada (para probar rechazos)."""
        return self._request(payload)

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        status, _, resp = self._request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}}})
        assert status == 200, resp
        if "error" in resp:
            raise AssertionError(resp["error"])
        blocks = resp.get("result", {}).get("content") or []
        text = next((b.get("text", "") for b in blocks
                     if b.get("type") == "text"), "{}")
        return json.loads(text)

    def tools(self) -> list[str]:
        _, _, resp = self._request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return [t["name"] for t in resp["result"]["tools"]]

    def delete_session(self) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Mcp-Session-Id": self.session or ""}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            conn.request("DELETE", "/mcp", headers=headers)
            return conn.getresponse().status
        finally:
            conn.close()
