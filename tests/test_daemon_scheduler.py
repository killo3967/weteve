"""Tests del planificador de recarga periódica."""

import threading
import time

from src.application.services.daemon.scheduler import (
    REFRESH_FLOOR_MIN,
    RefreshScheduler,
    target_interval_min,
)
from src.application.services.list_manager.models import PipelineTarget, SourceRef
from src.application.services.list_manager.source_catalog import SourceCatalog, SourceEntry


class TestTargetIntervalMin:
    def test_min_de_los_refresh_de_las_listas_activas(self):
        catalog = SourceCatalog([
            SourceEntry("a", "A", "m3u", "url", "http://a", refresh_min=30),
            SourceEntry("b", "B", "m3u", "url", "http://b", refresh_min=15),
            SourceEntry("c", "C", "epg", "url", "http://c", refresh_min=120),
        ])
        t = PipelineTarget(name="t", source_index="x",
                           source_refs=(SourceRef("a", "A"), SourceRef("b", "B")),
                           epg_refs=(SourceRef("c", "C"),))
        assert target_interval_min(catalog, t) == 15

    def test_suelo_de_cinco_minutos(self):
        catalog = SourceCatalog([
            SourceEntry("a", "A", "m3u", "url", "http://a", refresh_min=1),
        ])
        t = PipelineTarget(name="t", source_index="x",
                           source_refs=(SourceRef("a", "A"),))
        assert target_interval_min(catalog, t) == REFRESH_FLOOR_MIN

    def test_refresco_cero_o_inactivo_no_cuentan(self):
        catalog = SourceCatalog([
            SourceEntry("a", "A", "m3u", "url", "http://a", refresh_min=0),
            SourceEntry("b", "B", "m3u", "url", "http://b", refresh_min=60,
                        enabled=False),
        ])
        t = PipelineTarget(name="t", source_index="x",
                           source_refs=(SourceRef("a", "A"), SourceRef("b", "B")))
        assert target_interval_min(catalog, t) is None

    def test_target_sin_refs_es_estatico(self):
        t = PipelineTarget(name="t", source_index=0)
        assert target_interval_min(SourceCatalog([]), t) is None


class _TickClock:
    """Clock de test: tick inmediato, cuenta recargas y corta tras N ciclos."""

    def __init__(self, cycles: int):
        self.calls = 0
        self._cycles = cycles
        self._done = threading.Event()

    def __call__(self, stop_event, seconds):
        """Un tick: True = recargar, False = parar."""
        if stop_event.is_set():
            return False
        self.calls += 1
        if self.calls >= self._cycles:
            self._done.set()
            return False
        return True

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


class TestRefreshScheduler:
    def test_recarga_periodica_llama_al_reload(self):
        reloaded = []
        clock = _TickClock(3)
        sched = RefreshScheduler(reloaded.append, clock=clock)
        sched.schedule("t0", interval_min=1)
        assert clock.wait(2.0)
        assert len(reloaded) >= 2

    def test_cancel_detiene_el_ciclo(self):
        reloaded = []
        clock = _TickClock(10)
        sched = RefreshScheduler(reloaded.append, clock=clock)
        sched.schedule("t0", interval_min=1)
        sched.cancel("t0")
        time.sleep(0.1)
        n = len(reloaded)
        time.sleep(0.2)
        assert len(reloaded) == n

    def test_reschedule_reemplaza_el_ciclo_anterior(self):
        reloaded = []
        clock = _TickClock(4)
        sched = RefreshScheduler(reloaded.append, clock=clock)
        sched.schedule("t0", interval_min=1)
        sched.schedule("t0", interval_min=1)
        assert clock.wait(2.0)
        assert len(reloaded) >= 2

    def test_excepcion_del_reload_no_mata_el_hilo(self):
        calls = []
        clock = _TickClock(3)
        sched = RefreshScheduler(calls.append, clock=clock)

        def bad_reload(tid):
            calls.append(tid)
            raise RuntimeError("boom")

        # el clock rompe el loop tras N ciclos aunque reload falle
        sched._reload = bad_reload
        sched.schedule("t0", interval_min=1)
        assert clock.wait(2.0)
        assert len(calls) >= 1
