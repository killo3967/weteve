"""Mini-web del gestor de listas (localhost): sirve playlist.m3u y epg.xml.

Esperado por la especificación (odd/tasks/list-manager.md): salida pre-generada
y cacheada; los endpoints solo sirven el string cacheado. ThreadingHTTPServer
en hilo daemon, bind 127.0.0.1 por defecto.

Nota Windows: ``allow_reuse_address`` se desactiva a propósito. En Windows,
SO_REUSEADDR permite enlazar dos veces el mismo puerto, lo que impediría
detectar un puerto ocupado (ver ``http_common.NoReuseServer``).

Las peticiones de los clientes IPTV (``playlist.m3u`` y ``epg.xml``) se
registran a INFO mediante ``http_common.AccessLogMixin``.
"""

import logging
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.infrastructure.client.net_utils import announced_host
from src.infrastructure.web.http_common import AccessLogMixin, NoReuseServer

logger = logging.getLogger(__name__)

PLAYLIST_PATH = "/playlist.m3u"
EPG_PATH = "/epg.xml"
#: Prefijo de los logos que sirve el propio demonio (copia local en disco).
LOGOS_PREFIX = "/logos/"


def url_base_for(host: str, port: int) -> str:
    """URL con la que un reproductor alcanza el servidor de un target.

    ``0.0.0.0`` no es una dirección navegable: para la lista se usa la IP del
    equipo en la red local, que es lo que necesita el cliente IPTV. La misma
    regla vale para anunciar la playlist y los logos del propio demonio.
    """
    return f"http://{announced_host(host)}:{int(port)}"


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
        # Sin permisos de psutil caemos en el mismo "proceso desconocido"
        # del return final.
        pass
    return "proceso desconocido"


class PlaylistServer:
    """Servidor HTTP local que expone la M3U y el EPG de un target."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080,
                 logos_dir: Path | str | None = None):
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._playlist: str = ""
        self._epg: str = ""
        # logos servidos desde disco: la lista no depende de terceros
        self._logos_dir = Path(logos_dir) if logos_dir is not None else None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self._port

    @property
    def url_base(self) -> str:
        return url_base_for(self._host, self.port)

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def set_content(self, playlist: str, epg: str) -> None:
        """Actualiza el contenido servido (puede llamarse en caliente)."""
        self._playlist = playlist
        self._epg = epg

    def route(self, path: str) -> tuple[str | bytes, str] | None:
        """Resuelve una ruta a (cuerpo, content-type). None si desconocida."""
        if path.rstrip("/") == PLAYLIST_PATH:
            return self._playlist, "audio/x-mpegurl"
        if path.rstrip("/") == EPG_PATH:
            return self._epg, "application/xml"
        if path.startswith(LOGOS_PREFIX):
            return self._logo(path[len(LOGOS_PREFIX):])
        return None

    def _logo(self, nombre: str) -> tuple[bytes, str] | None:
        """Logo de ``assets/logos``; el nombre no puede salirse del directorio."""
        import mimetypes
        from urllib.parse import unquote

        if self._logos_dir is None:
            return None
        limpio = unquote(nombre).split("?")[0].strip()
        if not limpio or "/" in limpio or "\\" in limpio or ".." in limpio:
            return None
        try:
            datos = (self._logos_dir / limpio).read_bytes()
        except OSError:
            return None
        tipo = mimetypes.guess_type(limpio)[0] or "application/octet-stream"
        return datos, tipo

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
            return NoReuseServer((self._host, self._port), handler)
        except OSError as e:
            logger.error("No se pudo enlazar %s:%d: %s", self._host, self._port, e)
            return None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(AccessLogMixin, BaseHTTPRequestHandler):
            log_prefix = "web"

            def do_GET(self) -> None:
                resolved = server.route(self.path)
                if resolved is None:
                    self.send_error(404)
                    return
                body, ctype = resolved
                if isinstance(body, str):
                    data = body.encode("utf-8")
                    ctype = f"{ctype}; charset=utf-8"
                else:
                    data = body
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if not isinstance(body, str):
                    # un logo no cambia: que el reproductor no lo repida
                    self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
                # Cerrar con datos recién escritos da RST intermitente en el
                # loopback de Windows (~50% medido): el reproductor vería
                # ConnectionResetError. 20 ms de margen lo eliminan.
                time.sleep(0.02)

        return Handler
