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
import sys
import time
from http.server import ThreadingHTTPServer

logger = logging.getLogger(__name__)


class NoReuseServer(ThreadingHTTPServer):
    """ThreadingHTTPServer que no reutiliza direcciones (detecta puerto ocupado)."""

    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        """Excepción atendiendo una petición (RST de loopback, cierre brusco).

        Por defecto la librería imprime la traza en stderr, que el demonio
        lanzado por Task Scheduler no conserva. Se registra en el log.

        Un reset o un cierre abortado del cliente es rutina en el loopback de
        Windows (el cliente cierra mientras el servidor lee la petición
        siguiente): se anota en INFO y sin traza, que si no llena el log.
        """
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError)):
            logger.info("conexión con %s cerrada por el cliente (%s)",
                        client_address, type(error).__name__)
            return
        logger.warning("conexión con %s interrumpida", client_address,
                       exc_info=True)


class AccessLogMixin:
    """Log de acceso de un ``BaseHTTPRequestHandler``.

    ``log_message`` recibe la petición ya formateada por la librería
    (``"POST /mcp HTTP/1.1" 200 -``): aquí se antepone el prefijo del
    servicio y la IP del cliente. Cada subclase define ``log_prefix``.
    """

    log_prefix = "http"
    # Lo aporta BaseHTTPRequestHandler en las tres subclases.
    client_address: tuple[str, int]

    def parse_request(self) -> bool:  # noqa: D401 - hook de BaseHTTPRequestHandler
        """Marca el inicio: el log de acceso añade lo que tardó la petición."""
        from http.server import BaseHTTPRequestHandler
        from typing import cast

        self._t_inicio = time.perf_counter()
        # el mixin siempre se combina con BaseHTTPRequestHandler
        return BaseHTTPRequestHandler.parse_request(
            cast(BaseHTTPRequestHandler, self))

    def log_message(self, format: str, *args) -> None:
        inicio = getattr(self, "_t_inicio", None)
        ms = (time.perf_counter() - inicio) * 1000 if inicio else 0.0
        logger.info("%s: %s - %s (%.0f ms)", self.log_prefix,
                    self.client_address[0], format % args, ms)

    def log_error(self, format: str, *args) -> None:
        logger.warning("%s: %s - %s", self.log_prefix, self.client_address[0],
                       format % args)
