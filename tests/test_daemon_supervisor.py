"""Tests del TargetSupervisor: ciclo de vida de targets servidos por el daemon.

Cada test usa un PipelineTarget real pero con una fuente M3U mínima en
``tmp_path``; sin red. El supervisor debe gestionar un PlaylistServer por
target y permitir recargar contenido sin reiniciar el proceso.
"""

import configparser
import time
from pathlib import Path

import pytest
import urllib.request

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import FilterRule, PipelineTarget


def _get(url: str, attempts: int = 5) -> str:
    """GET con reintentos: en Windows una conexión loopback inmediata tras bind
    puede recibir RST (Defender/backlog); no es un defecto del servidor."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.read().decode("utf-8")
        except (ConnectionResetError, OSError) as e:
            last = e
            time.sleep(0.1)
    raise last  # type: ignore[misc]


def _write_m3u(path: Path, names: list[str]) -> str:
    lines = ["#EXTM3U"]
    for i, name in enumerate(names):
        lines.append(
            f'#EXTINF:-1 tvg-id="ch{i}" group-title="G{i%2}",{name}\n'
            f"http://stream.example/{i}.ts"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _target(m3u: str, port: int, name: str = "t") -> PipelineTarget:
    return PipelineTarget(
        name=name,
        source_index=m3u,
        rules=(FilterRule(field="group-title", pattern="{G0}"),),
        port=port,
    )


class TestTargetSupervisor:
    def test_start_and_serve_playlist(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            ok = sup.start_target("t0", _target(m3u, 0))
            assert ok
            info = sup.get_status()["targets"]["t0"]
            assert info["running"] is True
            url = info["playlist_url"]
            body = _get(url)
            assert "Canal A" in body  # G0 pasa el filtro {G0}
            assert "Canal B" not in body
        finally:
            sup.stop_all()

    def test_start_twice_replaces_content(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            assert sup.start_target("t0", _target(m3u, 0))
            target2 = _target(m3u, 0, name="t0-v2")
            assert sup.start_target("t0", target2)
            info = sup.get_status()["targets"]["t0"]
            assert info["name"] == "t0-v2"
        finally:
            sup.stop_all()

    def test_stop_target(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor()
        try:
            sup.start_target("t0", _target(m3u, 0))
            assert sup.stop_target("t0") is True
            status = sup.get_status()["targets"]["t0"]
            assert status["running"] is False
            # parar de nuevo es idempotente
            assert sup.stop_target("t0") is False
        finally:
            sup.stop_all()

    def test_stop_unknown_target(self) -> None:
        sup = TargetSupervisor()
        assert sup.stop_target("nope") is False

    def test_reload_target_rebuilds_content(self, tmp_path: Path) -> None:
        m3u_path = tmp_path / "src.m3u"
        m3u = _write_m3u(m3u_path, ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            sup.start_target("t0", _target(m3u, 0))
            # cambia la fuente en disco y recarga
            _write_m3u(m3u_path, ["Canal A", "Canal B", "Canal C"])
            assert sup.reload_target("t0") is True
            url = sup.get_status()["targets"]["t0"]["playlist_url"]
            assert "Canal C" in _get(url)
        finally:
            sup.stop_all()

    def test_reload_without_start_creates_it(self, tmp_path: Path) -> None:
        """Reload sobre target no arrancado: lo construye pero no sirve (serve=False)."""
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor()
        try:
            assert sup.reload_target("t0", _target(m3u, 0)) is True
            info = sup.get_status()["targets"]["t0"]
            assert info["running"] is False
            assert info["channel_count"] == 1
        finally:
            sup.stop_all()

    def test_port_conflict_reports_error(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor()
        try:
            sup.start_target("a", _target(m3u, 0))
            # puerto elegido dinámicamente por el primer server; forzamos colisión
            first_port = sup.get_status()["targets"]["a"]["port"]
            assert sup.start_target("b", _target(m3u, first_port)) is False
            assert "error" in sup.get_status()["targets"]["b"]
        finally:
            sup.stop_all()

    def test_preview_channels(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            sup.reload_target("t0", _target(m3u, 0))
            preview = sup.preview_channels("t0", limit=1)
            assert len(preview) == 1
            assert preview[0]["name"] == "Canal A"
        finally:
            sup.stop_all()

    def test_run_pipeline_metrics(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            sup.reload_target("t0", _target(m3u, 0))
            metrics = sup.run_pipeline("t0")
            assert metrics["total_input"] == 2
            assert metrics["filtered_count"] == 1
            # sin EPG registrada, todo canal filtrado cuenta como sin guía
            assert metrics["unmatched_count"] == 1
        finally:
            sup.stop_all()

    def test_config_error_is_reported(self, tmp_path: Path, monkeypatch) -> None:
        """Si el pipeline lanza, start_target devuelve False y el error queda en status."""
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor()

        def boom(target):
            raise ValueError("EPG inaccesible")

        monkeypatch.setattr(sup._pipeline, "run", boom)
        try:
            ok = sup.start_target("bad", _target(m3u, 0))
            assert ok is False
            st = sup.get_status()["targets"]["bad"]
            assert "EPG inaccesible" in st["error"]
        finally:
            sup.stop_all()


class TestSupervisorMultiFuente:
    def test_target_con_refs_sirve_la_union(self, tmp_path: Path) -> None:
        m1 = _write_m3u(tmp_path / "a.m3u", ["Canal A"])
        m2 = _write_m3u(tmp_path / "b.m3u", ["Canal B"])
        from src.application.services.list_manager.models import SourceRef
        t = PipelineTarget(
            name="multi", source_index="merged",
            source_refs=(SourceRef("s1", "A", m1), SourceRef("s2", "B", m2)),
            port=0,
        )
        sup = TargetSupervisor()
        try:
            assert sup.start_target("t0", t)
            body = _get(sup.get_status()["targets"]["t0"]["playlist_url"])
            assert "A.Canal A" in body   # prefijo por lista
            assert "B.Canal B" in body
        finally:
            sup.stop_all()

    def test_target_con_refs_puerto_cero_elige_libre(self, tmp_path: Path) -> None:
        m1 = _write_m3u(tmp_path / "a.m3u", ["Canal A"])
        from src.application.services.list_manager.models import SourceRef
        t = PipelineTarget(name="multi", source_index="merged",
                           source_refs=(SourceRef("s1", "A", m1),), port=0)
        sup = TargetSupervisor()
        try:
            assert sup.start_target("t0", t)
            url = sup.get_status()["targets"]["t0"]["playlist_url"]
            assert url  # hay servidor vivo en un puerto asignado
        finally:
            sup.stop_all()
