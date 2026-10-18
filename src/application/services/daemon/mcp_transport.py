"""Servidor HTTP único del demonio.

Sirve, en un solo puerto:

- ``/mcp``: MCP Streamable HTTP (JSON-RPC 2.0).
- ``/api/v1/...``: REST (frontends).
- ``/weteve/{id}/playlist.m3u`` y ``/epg.xml`` (más alias cortos y heredados):
  la lista y la guía servidas por el supervisor.
- Rutas de la API de control (``/health``, ``/status``, ``/shutdown``,
  ``/targets/{id}/reload``).

El token (si está definido) protege MCP, REST y control; la playlist queda
abierta a propósito porque los reproductores IPTV no mandan ``Authorization``.

Especificación MCP (Streamable HTTP) original del endpoint:

POST /mcp  → JSON-RPC request; 200 con cuerpo o 202 si notificación.
GET /mcp   → reservado a streams SSE (no usado: respondemos 405).
DELETE /mcp → cierra sesión (204).

Sesión por cabecera ``Mcp-Session-Id`` (asignada en initialize). Token
opcional vía ``Authorization: Bearer``. Mismo patrón de servidor que
PlaylistServer/ControlApiServer (ThreadingHTTPServer sin SO_REUSEADDR).

Log de comunicaciones (``logs/weteve-daemon.log``): una línea INFO por
petición —incluido el método JSON-RPC (``initialize``, ``tools/call``…) y el
cliente— y una WARNING por rechazo (token inválido, ruta desconocida, JSON
inválido, GET sin SSE).
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.infrastructure.web.http_common import AccessLogMixin, NoReuseServer

logger = logging.getLogger(__name__)


def _loopback_origin(origin: str) -> bool:
    """True si el Origin es de este equipo (UI web servida en localhost).

    CORS se abre solo a orígenes locales: un frontend en otro host debe
    servirse desde el propio demonio (mismo origen) o usar el token.
    """
    origin = (origin or "").strip().lower()
    if "://" not in origin:
        return False
    host = origin.split("://", 1)[1].split("/")[0].split(":")[0]
    return host in ("127.0.0.1", "localhost", "[::1]")

class McpHttpTransport:
    """Expone un McpStreamableServer sobre HTTP en su propio puerto."""

    def __init__(self, core: McpStreamableServer, host: str = "127.0.0.1",
                 port: int = 34401, token: str = "", rest_api=None,
                 control_api=None, playlist=None) -> None:
        self._core = core
        self._host = host
        self._port = port
        self._token = token
        # API REST v1 opcional en el mismo puerto (prefijo /api/v1).
        self._rest_api = rest_api
        # Rutas de control y playlist en el mismo puerto.
        self._control_api = control_api
        self._playlist = playlist  # callable(path) -> (status, body, ctype)|None
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
            self._server = NoReuseServer((self._host, self._port),
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
        rest_api = self._rest_api
        control_api = self._control_api
        playlist = self._playlist

        class Handler(AccessLogMixin, BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            log_prefix = "mcp"

            def _authorized(self) -> bool:
                if not token:
                    return True
                return self.headers.get("Authorization", "") == f"Bearer {token}"

            def _read_body(self) -> bytes:
                """Cuerpo de la petición (0 bytes si no lo hay)."""
                length = int(self.headers.get("Content-Length", 0) or 0)
                return self.rfile.read(length) if length else b""

            def do_POST(self) -> None:
                # El cuerpo se lee SIEMPRE, aunque la petición se rechace: si
                # no, cerrar el socket con datos sin leer provoca un RST en el
                # loopback de Windows y el cliente ve ConnectionResetError.
                body = self._read_body()
                if rest_api is not None and self.path.startswith("/api/v1"):
                    self._rest("POST", body)
                    return
                if control_api is not None and not self.path.startswith("/mcp"):
                    self._control("POST")
                    return
                if self.path != "/mcp":
                    logger.warning("mcp: ruta desconocida %s desde %s",
                                   self.path, self.client_address[0])
                    self._json(404, {"error": "ruta desconocida"})
                    return
                if not self._authorized():
                    logger.warning("mcp: token inválido o ausente desde %s",
                                   self.client_address[0])
                    self._json(401, {"error": "token requerido"})
                    return
                try:
                    message = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    logger.warning("mcp: cuerpo no-JSON (%d bytes) desde %s",
                                   len(body), self.client_address[0])
                    self._json(400, {"jsonrpc": "2.0", "id": None, "error": {
                        "code": -32700, "message": "JSON inválido"}})
                    return

                session_id = self.headers.get("Mcp-Session-Id")
                logger.info("mcp: %s (%s) desde %s",
                            message.get("method", "?") if isinstance(message, dict)
                            else "lote",
                            f"sesión {session_id}" if session_id else "sin sesión",
                            self.client_address[0])
                resp, new_session = core.handle_request(message, session_id)
                if resp is None:
                    # notificación: 202 sin cuerpo
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._json(200, resp, session=new_session)

            def do_PUT(self) -> None:
                body = self._read_body()
                if rest_api is not None and self.path.startswith("/api/v1"):
                    self._rest("PUT", body)
                    return
                logger.warning("api: PUT %s no soportado desde %s",
                               self.path, self.client_address[0])
                self._json(405, {"error": "método PUT no soportado"})

            def do_DELETE(self) -> None:
                if rest_api is not None and self.path.startswith("/api/v1"):
                    # la API v1 no borra nada todavía: 405 explícito
                    self._rest("DELETE", self._read_body())
                    return
                if not self._authorized():
                    logger.warning("mcp: token inválido o ausente desde %s",
                                   self.client_address[0])
                    self._json(401, {"error": "token requerido"})
                    return
                logger.info("mcp: cierre de sesión %s desde %s",
                            self.headers.get("Mcp-Session-Id") or "?",
                            self.client_address[0])
                core.handle_delete(self.headers.get("Mcp-Session-Id"))
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_OPTIONS(self) -> None:
                """Preflight CORS de la API (solo orígenes de loopback)."""
                if rest_api is None or not self.path.startswith("/api/v1"):
                    self._json(405, {"error": "método OPTIONS no soportado"})
                    return
                if not self._authorized():
                    self._json(401, {"error": "token requerido"})
                    return
                self.send_response(204)
                self.send_header("Allow", "GET, PUT, POST, OPTIONS")
                origin = self.headers.get("Origin", "")
                if _loopback_origin(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Methods",
                                     "GET, PUT, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers",
                                     "Content-Type, Authorization")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                # Streamable HTTP permite GET para streams SSE; no lo usamos.
                # El GET se reparte entre REST, playlist y control.
                if rest_api is not None and self.path.startswith("/api/v1"):
                    self._rest("GET", None)
                    return
                if playlist is not None:
                    route = playlist(self.path)
                    if route is not None:
                        status, text, ctype = route
                        self._text(status, text, ctype)
                        return
                if control_api is not None:
                    self._control("GET")
                    return
                logger.warning("mcp: GET %s no soportado (sin SSE) desde %s",
                               self.path, self.client_address[0])
                self._json(405, {"error": "GET no soportado (sin stream SSE)"})

            def _text(self, status: int, body: str, ctype: str) -> None:
                """Respuesta de texto (playlist/EPG)."""
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                time.sleep(0.02)  # ver _rest: el cierre inmediato da RST

            def _control(self, method: str) -> None:
                """Rutas de la API de control (también exigen token)."""
                if not self._authorized():
                    logger.warning("control: token inválido o ausente desde %s",
                                   self.client_address[0])
                    self._json(401, {"error": "token requerido"})
                    return
                status, payload = control_api.handle(method, self.path)
                self._json(status, payload)

            def _rest(self, method: str, body: bytes | None) -> None:
                """Atiende /api/v1/* con la misma autorización que /mcp."""
                if not self._authorized():
                    logger.warning("api: token inválido o ausente desde %s",
                                   self.client_address[0])
                    self._json(401, {"error": "token requerido"})
                    return
                status, payload = rest_api.handle(method, self.path, body)
                self._json(status, payload)

            def _json(self, status: int, body: dict,
                      session: str | None = None) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                origin = self.headers.get("Origin", "")
                if rest_api is not None and _loopback_origin(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.end_headers()
                self.wfile.write(data)
                # ver _settle(): cerrar con datos recién escritos da RST
                time.sleep(0.02)

        return Handler
