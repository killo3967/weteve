"""Mini-web del gestor de listas (localhost): sirve playlist.m3u y epg.xml.

Esperado por la especificación (odd/tasks/list-manager.md): salida pre-generada
y cacheada; los endpoints solo sirven el string cacheado. ThreadingHTTPServer
en hilo daemon, bind 127.0.0.1 por defecto.

Nota Windows: ``allow_reuse_address`` se desactiva a propósito. En Windows,
SO_REUSEADDR permite enlazar dos veces el mismo puerto, lo que impediría
detectar un puerto ocupado.
"""

import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

PLAYLIST_PATH = "/playlist.m3u"
EPG_PATH = "/epg.xml"


class _NoReuseServer(ThreadingHTTPServer):
    allow_reuse_address = False


def port_in_use(host: str, port: int) -> bool:
    """Comprueba si ``host:port`` está ocupado intentando enlazar con SO_REUSEADDR."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # SO_REUSEADDR permite sondear el puerto sin arrebatar el bind a su
            # dueño (a diferencia del allow_reuse_address=False del servidor).
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return False
    except OSError:
        return True


def find_port_owner(port: int, host: str = "127.0.0.1") -> str | None:
    """Describe el proceso que escucha en ``port``; None si el puerto está libre.

    Requiere psutil para identificar el proceso; sin él, un puerto ocupado
    devuelve una descripción genérica.
    """
    if not port_in_use(host, port):
        return None
    try:
        import psutil
    except ImportError:
        return "proceso desconocido"

    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if conn.laddr.port != port:
                continue
            if conn.laddr.ip not in ("0.0.0.0", "::") and conn.laddr.ip != host:
                continue
            try:
                proc = psutil.Process(conn.pid)
                return f"{proc.name()} (PID {conn.pid})"
            except (psutil.Error, TypeError):
                return f"PID {conn.pid}" if conn.pid else "proceso desconocido"
    except psutil.Error:
        pass
    return "proceso desconocido"


class PlaylistServer:
    """Servidor HTTP local que expone la M3U y el EPG de un target."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._playlist: str = ""
        self._epg: str = ""

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self._port

    @property
    def url_base(self) -> str:
        # 0.0.0.0 no es una IP navegable: mostrar la del equipo en la red local.
        display_host = self._host
        if self._host in ("0.0.0.0", "::"):
            try:
                import socket

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    display_host = s.getsockname()[0]
            except OSError:
                display_host = "<ip-local>"
        return f"http://{display_host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def set_content(self, playlist: str, epg: str) -> None:
        """Actualiza el contenido servido (puede llamarse en caliente)."""
        self._playlist = playlist
        self._epg = epg

    def route(self, path: str) -> tuple[str, str] | None:
        """Resuelve una ruta a (cuerpo, content-type). None si desconocida."""
        if path.rstrip("/") == PLAYLIST_PATH:
            return self._playlist, "audio/x-mpegurl"
        if path.rstrip("/") == EPG_PATH:
            return self._epg, "application/xml"
        return None

    def start(self) -> bool:
        """Arranca el servidor en un hilo daemon. False si el puerto está ocupado."""
        if self._server is not None:
            return True
        server = self._make_server()
        if server is None:
            logger.warning("Puerto %d ocupado; servicio no iniciado", self._port)
            return False
        self._server = server
        import threading

        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        logger.info("Gestor de listas sirviendo en %s", self.url_base)
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        logger.info("Gestor de listas: servicio detenido")

    def _make_server(self) -> ThreadingHTTPServer | None:
        handler = self._make_handler()
        try:
            return _NoReuseServer((self._host, self._port), handler)
        except OSError as e:
            logger.error("No se pudo enlazar %s:%d: %s", self._host, self._port, e)
            return None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                resolved = server.route(self.path)
                if resolved is None:
                    self.send_error(404)
                    return
                body, ctype = resolved
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args) -> None:
                logger.debug("web: %s", format % args)

        return Handler
