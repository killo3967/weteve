"""DaemonClient: cliente HTTP de la API de control del daemon.

Usado por la UI Qt (panel «Demonio») y por la CLI (``daemon.py status``).
Sin dependencias: ``http.client``. ``ping()`` nunca lanza — devuelve False si no
hay daemon escuchando (la UI lo usa para decidir si ofrece arranque local).

Los fallos se registran a WARNING (``daemon GET /health: sin respuesta en
http://127.0.0.1:34400 tras 4 intentos``): antes eran silenciosos y la UI
mostraba un error sin rastro en ``logs/weteve.log``.
"""

import http.client
import json
import logging

logger = logging.getLogger(__name__)

class DaemonClient:
    """Cliente JSON de la API de control (health/status/reload/shutdown)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 34400,
                 token: str = "", timeout: float = 5.0,
                 retries: int = 3, retry_delay: float = 0.2) -> None:
        self._host = host
        self._port = port
        self._base = f"http://{host}:{port}"
        self._token = token
        self._timeout = timeout
        self._retries = retries
        self._retry_delay = retry_delay
        # Un rechazo/timeout de loopback cuesta ~2 s: tras un fallo no se
        # reintenta durante unos segundos (la UI debe abrir con el demonio parado).
        self._down_until = 0.0
        self._down_seconds = 5.0

    @property
    def base_url(self) -> str:
        """URL base de la API de control (para mensajes de log)."""
        return self._base

    # -- helpers ---------------------------------------------------------------

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> tuple[int, dict | None]:
        """Devuelve (status, body). status 0 = daemon no accesible.

        ``http.client`` en vez de ``urllib``: urllib resuelve la configuración
        de proxy de Windows en cada intento (~2 s con el daemon caído, que es
        lo que congelaba la UI). Reintenta en errores de conexión (RST
        esporádico del loopback en Windows), no en respuestas HTTP.
        """
        import time

        if time.monotonic() < self._down_until:
            return 0, None
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        last_err: Exception | None = None
        attempts = 0
        for attempt in range(self._retries + 1):
            attempts = attempt + 1
            conn = http.client.HTTPConnection(self._host, self._port,
                                              timeout=self._timeout)
            try:
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8")
                self._down_until = 0.0  # responde otra vez
                if resp.status >= 400:
                    # el demonio responde pero rechaza: token, ruta o target
                    logger.warning("daemon %s %s -> HTTP %d en %s",
                                   method, path, resp.status, self._base)
                return resp.status, (json.loads(raw) if raw else None)
            except (ConnectionRefusedError, TimeoutError) as e:
                # nada escuchando o sin respuesta: reintentar no cambia nada (y
                # en Windows cada intento a loopback tarda ~2 s)
                last_err = e
                self._down_until = time.monotonic() + self._down_seconds
                break
            except (ConnectionError, OSError) as e:
                last_err = e
                if attempt < self._retries:
                    time.sleep(self._retry_delay)
            finally:
                conn.close()
        logger.warning("daemon %s %s: sin respuesta en %s tras %d intento(s): %s",
                       method, path, self._base, attempts, last_err)
        return 0, None

    def ping(self) -> bool:
        """True si hay un daemon con API de control respondiendo."""
        status, _ = self._request("GET", "/health")
        return status == 200

    def status(self) -> dict | None:
        """Estado agregado del daemon, o None si no responde."""
        status, body = self._request("GET", "/status")
        return body if status == 200 else None

    def reload_target(self, target_id: str) -> bool:
        status, _ = self._request("POST", f"/targets/{target_id}/reload")
        return status == 200

    def shutdown(self) -> bool:
        status, _ = self._request("POST", "/shutdown")
        return status == 200
