"""API de control HTTP del daemon: JSON mínimo para la UI Qt.

Endpoints (localhost):
  GET  /health              → {"ok": true}
  GET  /status              → estado agregado del supervisor
  POST /targets/{id}/reload → reejecuta el pipeline del target
  POST /shutdown            → marca el flag de parada del daemon

El resto del control (CRUD de targets incluido) vive en el MCP; esta API es
solo lo mínimo que la UI necesita sin pasar por MCP. Comparte el patrón de
servidor con PlaylistServer (ThreadingHTTPServer, sin SO_REUSEADDR).
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

class _NoReuseServer(ThreadingHTTPServer):
    allow_reuse_address = False

class ControlApiServer:
    """Servidor HTTP de control. ``on_shutdown`` se invoca en POST /shutdown."""

    def __init__(self, supervisor, host: str = "127.0.0.1", port: int = 34400,
                 token: str = "", on_shutdown=None, target_store=None) -> None:
        self._supervisor = supervisor
        # si hay store, el reload toma la definición de config.ini (fresca)
        # en vez de la que el supervisor memorizó al arrancar
        self._store = target_store
        self._host = host
        self._port = port
        self._token = token
        self._on_shutdown = on_shutdown
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
        """Arranca el servidor en un hilo daemon. False si el puerto está ocupado."""
        if self._server is not None:
            return True
        try:
            self._server = _NoReuseServer((self._host, self._port), self._make_handler())
        except OSError as e:
            logger.error("API de control: no se pudo enlazar %s:%d: %s",
                         self._host, self._port, e)
            return False
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        logger.info("API de control en %s", self.url_base)
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        logger.info("API de control detenida")

    # -- config compartida ------------------------------------------------------

    def _fresh_target(self, target_id: str):
        """Definición actualizada de config.ini, o None si no hay store.

        Sin store (o con uno antiguo sin reload()) se reutiliza lo que ya
        tiene el supervisor: same reload de pipeline de toda la vida.
        """
        if self._store is None:
            return None
        try:
            return self._store.reload(target_id)
        except Exception:  # noqa: BLE001 - store de test o fichero ilegible
            return self._store.get(target_id)

    def _fresh_epg_path(self, target_id: str):
        if self._store is None:
            return None
        return self._store.get_epg_path(target_id)

    # -- handler -------------------------------------------------------------

    def _handle(self, method: str, path: str, auth_ok: bool) -> tuple[int, dict]:
        if self._token and not auth_ok:
            return 401, {"error": "token requerido"}
        if method == "GET":
            if path == "/health":
                return 200, {"ok": True}
            if path == "/status":
                return 200, self._supervisor.get_status()
        if method == "POST":
            if path == "/shutdown":
                if self._on_shutdown is not None:
                    self._on_shutdown()
                return 200, {"ok": True}
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "targets" and parts[2] == "reload":
                target_id = parts[1]
                fresh = self._fresh_target(target_id)
                defined = (fresh is not None
                           or target_id in self._supervisor.get_status()["targets"])
                if not defined:
                    return 404, {"error": f"target {target_id} no existe"}
                ok = self._supervisor.reload_target(
                    target_id, fresh, epg_path=self._fresh_epg_path(target_id))
                return (200, {"ok": True}) if ok else (500, {"ok": False})
        return 404, {"error": "ruta desconocida"}

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _dispatch(self) -> None:
                auth = self.headers.get("Authorization", "")
                auth_ok = bool(api._token) and auth == f"Bearer {api._token}"
                status, body = api._handle(self.command, self.path, auth_ok)
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _dispatch
            do_POST = _dispatch

            def log_message(self, format: str, *args) -> None:
                logger.debug("control: %s", format % args)

        return Handler
