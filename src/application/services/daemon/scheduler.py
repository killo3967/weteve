"""Planificador de recarga periódica de targets del daemon.

Cada target con fuentes de refresco > 0 se recarga en un hilo a intervalos
de ``min(refrescos activos de sus listas)``, con suelo de REFRESH_FLOOR_MIN
minutos (el pipeline tarda segundos por lista y el servidor no debe rearmarse
sin fin).
"""

import logging
import threading

from src.application.services.list_manager.source_catalog import SourceCatalog

logger = logging.getLogger(__name__)

REFRESH_FLOOR_MIN = 5


def target_interval_min(catalog: SourceCatalog, target) -> int | None:
    """Intervalo de recarga para un target, o None si no refresca.

    El mínimo de los refresh_min de las listas IPTV/EPG activas referenciadas
    por el target, con suelo de REFRESH_FLOOR_MIN. 0 o None = estática.
    """
    ids = {r.id for r in target.source_refs} | {r.id for r in target.epg_refs}
    if not ids:
        return None
    mins = [
        e.refresh_min
        for e in catalog.all()
        if e.id in ids and e.enabled and e.refresh_min > 0
    ]
    if not mins:
        return None
    return max(min(mins), REFRESH_FLOOR_MIN)


class RefreshScheduler:
    """Hilos de recarga periódica por target."""

    def __init__(self, reload_fn, clock=None):
        """``reload_fn(target_id)`` se llama en cada ciclo; ``clock`` permite
        inyectar una función de espera en tests (por defecto ``Event.wait``)."""
        self._reload = reload_fn
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._clock = clock or (lambda ev, sec: not ev.wait(sec))

    def schedule(self, target_id: str, interval_min: int) -> None:
        """(Re)programa la recarga de un target; detiene el ciclo anterior."""
        self.cancel(target_id)
        stop = threading.Event()
        thread = threading.Thread(
            target=self._loop, args=(target_id, interval_min * 60, stop),
            name=f"refresh-{target_id}", daemon=True,
        )
        with self._lock:
            self._stops[target_id] = stop
            self._threads[target_id] = thread
        thread.start()
        logger.info("Recarga de %s programada cada %d min", target_id,
                    interval_min)

    def cancel(self, target_id: str) -> None:
        """Detiene el ciclo del target si existe."""
        with self._lock:
            stop = self._stops.pop(target_id, None)
            thread = self._threads.pop(target_id, None)
        if stop:
            stop.set()

    def cancel_all(self) -> None:
        """Detiene todos los ciclos."""
        with self._lock:
            stops = list(self._stops.values())
            self._stops.clear()
            self._threads.clear()
        for stop in stops:
            stop.set()

    def _loop(self, target_id: str, interval_s: int, stop: threading.Event) -> None:
        # el clock controla la espera (real Event.wait o tick de test);
        # devolver False significa stop pedido
        while self._clock(stop, interval_s):
            try:
                self._reload(target_id)
            except Exception:  # noqa: BLE001 - un ciclo fallido no mata el hilo
                logger.exception("Recarga programada de %s falló", target_id)
