"""Muestreo barato de los streams servidos: ¿la línea del proveedor responde?

No es un *healthcheck* completo: coge unos pocos canales ya servidos, pide la
cabecera con timeout corto y guarda el resultado en caché. Sirve para darse
cuenta de que la lista del proveedor ha dejado de servir (rotación de host,
cuenta caída, CDN caída) sin esperar a que el reproductor falle.

Dos decisiones que importan:

- **Nunca bloquea la API.** El sondeo corre en un hilo propio; ``status()``
  devuelve lo último que se sabe y, si toca refrescar, lo lanza en segundo
  plano. Una petición HTTP de la GUI no puede quedarse esperando al proveedor.
- **Muestreo, no barrido.** Tres canales repartidos por la lista bastan para
  saber si el origen responde; comprobar 800 costaría minutos y el proveedor lo
  tomaría por abuso (ver riesgo de T09 en la auditoría).
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: Canales que se comprueban por vuelta.
SAMPLE = 3
#: Cuánto vale un resultado antes de volver a sondear.
TTL_SECONDS = 300.0
#: Timeout por petición: si el proveedor no contesta rápido, está caído.
TIMEOUT_SECONDS = 5.0
#: Cabecera propia: muchos proveedores rechazan clientes sin identificar,
#: así que se mantiene la forma de un User-Agent de navegador.
_USER_AGENT = "Mozilla/5.0 (compatible; weteve/1.0)"


def probe_url(url: str, timeout: float = TIMEOUT_SECONDS) -> bool:
    """True si ``url`` responde algo que no sea un error de cliente/servidor.

    Se intenta ``HEAD`` y, si el proveedor lo rechaza (405/501 son legales para
    un stream), se pide un byte con ``Range``: así no se descarga el canal
    entero solo para saber si existe.
    """
    if not url:
        return False
    for method, extra in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        peticion = urllib.request.Request(
            url, method=method,
            headers={"User-Agent": _USER_AGENT, **extra})
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
                return int(respuesta.status) < 400
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue    # el proveedor no admite HEAD: se prueba con GET
            return exc.code < 400
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.debug("stream %s no responde: %s", url, exc)
            return False
    return False


def sample_urls(urls: list[str], sample: int = SAMPLE) -> list[str]:
    """Coge hasta ``sample`` urls repartidas por la lista.

    Repartidas, no las primeras: una caída de un host concreto se ve antes en
    canales distintos que en tres seguidos de la misma lista.
    """
    limpias = [u for u in urls if u]
    if len(limpias) <= sample:
        return limpias
    paso = len(limpias) / sample
    return [limpias[int(i * paso)] for i in range(sample)]


class StreamProbe:
    """Caché por perfil de "cuántos de los muestreados responden"."""

    def __init__(self, checker=None, sample: int = SAMPLE,
                 ttl: float = TTL_SECONDS) -> None:
        self._checker = checker if checker is not None else probe_url
        self._sample = sample
        self._ttl = ttl
        self._lock = threading.Lock()
        self._results: dict[str, dict] = {}
        self._inflight: set[str] = set()

    def status(self, key: str, urls: list[str]) -> dict:
        """Último resultado conocido del perfil ``key`` (y refresco si toca).

        Con ``checker`` inyectado, el sondeo se hace en el acto: es lo que
        usan los tests, sin hilos ni red.
        """
        if self._sin_muestras(urls):
            return {"checked": 0, "ok": 0, "pending": False}
        pendiente = {"checked": 0, "ok": 0, "pending": True}
        asincrono = self._checker is probe_url
        with self._lock:
            ultimo = self._results.get(key)
            if ultimo is not None and (
                    time.monotonic() - ultimo.get("at", 0.0)) < self._ttl:
                return dict(ultimo)
            anterior: dict = dict(ultimo) if ultimo is not None else pendiente
            if key in self._inflight:
                return anterior
            if asincrono:
                self._inflight.add(key)
        # Fuera del candado: ``_run`` lo vuelve a tomar al guardar el resultado.
        if not asincrono:
            return dict(self._run(key, urls))
        threading.Thread(target=self._run, args=(key, urls),
                         name=f"stream-probe-{key}", daemon=True).start()
        return anterior

    def reset(self) -> None:
        """Olvida los resultados (al parar un perfil o en los tests)."""
        with self._lock:
            self._results.clear()

    # -- interno -------------------------------------------------------------

    def _sin_muestras(self, urls: list[str]) -> bool:
        return not any(urls)

    def _run(self, key: str, urls: list[str]) -> dict:
        try:
            elegidas = sample_urls(list(urls), self._sample)
            respuestas = [bool(self._checker(url)) for url in elegidas]
            resultado = {"checked": len(respuestas),
                         "ok": sum(1 for r in respuestas if r),
                         "pending": False, "at": time.monotonic()}
            if respuestas and not any(respuestas):
                logger.warning("ninguno de los %d streams muestreados del "
                               "perfil %s responde: revisa la fuente",
                               len(respuestas), key)
        except Exception:  # noqa: BLE001 - el sondeo no puede tumbar /status
            logger.warning("error muestreando streams del perfil %s",
                           key, exc_info=True)
            resultado = {"checked": 0, "ok": 0, "pending": False,
                         "at": time.monotonic()}
        with self._lock:
            self._results[key] = resultado
            self._inflight.discard(key)
        return resultado
