"""Transporte HTTP del endpoint MCP (Streamable HTTP, spec 2025-03-26).

POST /mcp  → JSON-RPC request; 200 con cuerpo o 202 si notificación.
GET /mcp   → reservado a streams SSE (no usado: respondemos 405).
DELETE /mcp → cierra sesión (204).

Sesión por cabecera ``Mcp-Session-Id`` (asignada en initialize). Token
opcional vía ``Authorization: Bearer``. Mismo patrón de servidor que
PlaylistServer/ControlApiServer (ThreadingHTTPServer sin SO_REUSEADDR).
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.application.services.daemon.mcp_server import McpStreamableServer

logger = logging.getLogger(__name__)

class _NoReuseServer(ThreadingHTTPServer):
    allow_reuse_address = False

class McpHttpTransport:
    """Expone un McpStreamableServer sobre HTTP en su propio puerto."""

    def __init__(self, core: McpStreamableServer, host: str = "127.0.0.1",
                 port: int = 34401, token: str = "") -> None:
        self._core = core
        self._host = host
        self._port = port
        self._token = token
        self._server: ThreadingHTTPServer | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self._port

    @property
    def url_base(self) -> str:
        return f"http://{self._host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        if self._server is not None:
            return True
        try:
            self._server = _NoReuseServer((self._host, self._port),
                                          self._make_handler())
        except OSError as e:
            logger.error("MCP: no se pudo enlazar %s:%d: %s", self._host, self._port, e)
            return False
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        logger.info("MCP Streamable HTTP en %s/mcp", self.url_base)
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        logger.info("MCP transport detenido")

    # -- handler -------------------------------------------------------------

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        core = self._core
        token = self._token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _authorized(self) -> bool:
                if not token:
                    return True
                return self.headers.get("Authorization", "") == f"Bearer {token}"

            def do_POST(self) -> None:
                if self.path != "/mcp":
                    self._json(404, {"error": "ruta desconocida"})
                    return
                if not self._authorized():
                    self._json(401, {"error": "token requerido"})
                    return
                length = int(self.headers.get("Content-Length", 0))
                try:
                    message = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._json(400, {"jsonrpc": "2.0", "id": None, "error": {
                        "code": -32700, "message": "JSON inválido"}})
                    return

                session_id = self.headers.get("Mcp-Session-Id")
                resp, new_session = core.handle_request(message, session_id)
                if resp is None:
                    # notificación: 202 sin cuerpo
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._json(200, resp, session=new_session)

            def do_DELETE(self) -> None:
                if not self._authorized():
                    self._json(401, {"error": "token requerido"})
                    return
                core.handle_delete(self.headers.get("Mcp-Session-Id"))
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                # Streamable HTTP permite GET para streams SSE; no lo usamos.
                self._json(405, {"error": "GET no soportado (sin stream SSE)"})

            def _json(self, status: int, body: dict,
                      session: str | None = None) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args) -> None:
                logger.debug("mcp: %s", format % args)

        return Handler
