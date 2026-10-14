"""Piezas comunes de los tres servidores HTTP locales de weteve.

Los tres (``PlaylistServer``, ``ControlApiServer`` y ``McpHttpTransport``)
comparten dos preocupaciones:

- **``NoReuseServer``**: en Windows ``SO_REUSEADDR`` permite enlazar dos
  veces el mismo puerto, lo que ocultaría que ya hay otro proceso
  escuchando. Desactivarlo es lo que hace fiable detectar el puerto ocupado.
- **``AccessLogMixin``**: log de acceso. Sin él, ``BaseHTTPRequestHandler``
  escribe las peticiones a stderr en nivel DEBUG y las conexiones entrantes
  no aparecen en ``logs/weteve-daemon.log``. Aquí cada petición deja una
  línea INFO (cliente, petición y código de estado) y los errores de
  protocolo o de socket una línea WARNING.
"""

from __future__ import annotations

import logging
from http.server import ThreadingHTTPServer

logger = logging.getLogger(__name__)


class NoReuseServer(ThreadingHTTPServer):
    """ThreadingHTTPServer que no reutiliza direcciones (detecta puerto ocupado)."""

    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        """Excepción atendiendo una petición (RST de loopback, cierre brusco).

        Por defecto la librería imprime la traza en stderr, que el demonio
        lanzado por Task Scheduler no conserva. Se registra en el log.
        """
        logger.warning("conexión con %s interrumpida", client_address,
                       exc_info=True)


class AccessLogMixin:
    """Log de acceso de un ``BaseHTTPRequestHandler``.

    ``log_message`` recibe la petición ya formateada por la librería
    (``"POST /mcp HTTP/1.1" 200 -``): aquí se antepone el prefijo del
    servicio y la IP del cliente. Cada subclase define ``log_prefix``.
    """

    log_prefix = "http"

    def log_message(self, format: str, *args) -> None:
        logger.info("%s: %s - %s", self.log_prefix, self.client_address[0],
                    format % args)

    def log_error(self, format: str, *args) -> None:
        logger.warning("%s: %s - %s", self.log_prefix, self.client_address[0],
                       format % args)
