"""DaemonClient: cliente HTTP de la API de control del daemon.

Usado por la UI Qt (panel «Demonio») y por la CLI (``daemon.py status``).
Sin dependencias: urllib. ``ping()`` nunca lanza — devuelve False si no
hay daemon escuchando (la UI lo usa para decidir si ofrece arranque local).
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

class DaemonClient:
    """Cliente JSON de la API de control (health/status/reload/shutdown)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 34400,
                 token: str = "", timeout: float = 5.0,
                 retries: int = 3, retry_delay: float = 0.2) -> None:
        self._base = f"http://{host}:{port}"
        self._token = token
        self._timeout = timeout
        self._retries = retries
        self._retry_delay = retry_delay

    # -- helpers ---------------------------------------------------------------

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> tuple[int, dict | None]:
        """Devuelve (status, body). status 0 = daemon no accesible.

        Reintenta en errores de conexión (no en respuestas HTTP): el loopback
        de Windows puede devolver RST esporádico en conexiones inmediatas.
        """
        import time
        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(self._base + path, data=data,
                                         method=method)
            req.add_header("Content-Type", "application/json")
            if self._token:
                req.add_header("Authorization", f"Bearer {self._token}")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                return e.code, None
            except (urllib.error.URLError, OSError) as e:
                last_err = e
                if attempt < self._retries:
                    time.sleep(self._retry_delay)
        logger.debug("daemon %s %s inaccesible: %s", method, path, last_err)
        return 0, None

    # -- API -----------------------------------------------------------------

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
