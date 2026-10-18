"""Supervisor: lecturas sin esperar al pipeline y ejecución serializada.

Antes había un solo lock: `get_status` (y cualquier lectura) esperaba detrás
de un pipeline completo (3-8 s con fuentes grandes). Ahora el estado va por
`_lock` y la ejecución por `_run_lock`.
"""

import threading
import time

import pytest

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import (
    MappedChannel,
    PipelineTarget,
)
from src.application.services.list_manager.pipeline import PipelineResult


class _SlowPipeline:
    """Pipeline falso: tarda y registra cuántas ejecuciones se solapan."""

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.running = 0
        self.max_running = 0
        self.runs = 0

    def register_source(self, *a, **k) -> None:
        pass

    def register_merged(self, *a, **k) -> None:
        pass

    def run(self, target):
        self.runs += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            time.sleep(self.delay)
            return _result()
        finally:
            self.running -= 1

    def get_epg(self, key):
        return None


def _result() -> PipelineResult:
    served = [MappedChannel("Canal A", "http://s/a", matched_epg_id="a.tv")]
    return PipelineResult(
        served, 4, [],
        excluded_disabled=[MappedChannel("Apagado", "http://s/d")],
        excluded_no_epg=[MappedChannel("Canal B", "http://s/b"),
                         MappedChannel("Descartado", "http://s/c")],
        require_epg=True,
    )


@pytest.fixture
def supervisor():
    sup = TargetSupervisor()
    sup._pipeline = _SlowPipeline(delay=0.4)
    return sup


def _target(port: int = 0) -> PipelineTarget:
    return PipelineTarget(name="t", source_index="C:/x.m3u", port=port)


class TestLecturasNoEsperan:
    def test_get_status_responde_mientras_corre_el_pipeline(self, supervisor):
        t = threading.Thread(target=supervisor.start_target,
                             args=("0", _target()), daemon=True)
        t.start()
        time.sleep(0.1)  # el pipeline está dentro de run()
        start = time.perf_counter()
        status = supervisor.get_status()
        elapsed = time.perf_counter() - start
        assert elapsed < 0.15, f"get_status tardó {elapsed:.2f}s"
        assert "0" in status["targets"]
        t.join()
        assert supervisor.get_status()["targets"]["0"]["running"] is True

    def test_lecturas_ven_el_estado_anterior_durante_una_recarga(self, supervisor):
        supervisor.start_target("0", _target())
        t = threading.Thread(target=supervisor.reload_target, args=("0",),
                             daemon=True)
        t.start()
        time.sleep(0.1)
        assert supervisor.list_tv_channels("0")["total"] == 1
        t.join()


class TestEjecucionSerializada:
    def test_dos_recargas_no_se_solapan(self, supervisor):
        supervisor.start_target("0", _target())
        threads = [threading.Thread(target=supervisor.reload_target, args=("0",))
                   for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert supervisor._pipeline.max_running == 1

    def test_run_pipeline_en_paralelo_tampoco(self, supervisor):
        supervisor.start_target("0", _target())
        threads = [threading.Thread(target=supervisor.run_pipeline, args=("0",))
                   for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert supervisor._pipeline.max_running == 1


class TestSnapshot:
    def test_mapping_candidates_sale_del_snapshot(self, supervisor):
        supervisor.start_target("0", _target())
        runs = supervisor._pipeline.runs
        data = supervisor.mapping_candidates("0")
        assert data["total"] == 3
        names = [c["name"] for c in data["channels"]]
        assert names == ["Canal B", "Descartado", "Apagado"]
        assert data["channels"][2]["disabled"] is True
        assert supervisor._pipeline.runs == runs

    def test_mapping_candidates_sin_ejecutar_vacio(self, supervisor):
        assert supervisor.mapping_candidates("nada")["total"] == 0

    def test_list_tv_channels_no_reejecuta(self, supervisor):
        supervisor.start_target("0", _target())
        runs = supervisor._pipeline.runs
        assert supervisor.list_tv_channels("0")["total"] == 1
        assert supervisor.list_tv_channels("0")["total"] == 1
        assert supervisor._pipeline.runs == runs

    def test_metrics_sin_reejecutar(self, supervisor):
        supervisor.start_target("0", _target())
        runs = supervisor._pipeline.runs
        assert supervisor.metrics("0")["filtered_count"] == 1
        assert supervisor._pipeline.runs == runs
        assert "error" in supervisor.metrics("nada")
