"""Tests del TargetSupervisor: ciclo de vida de los targets servidos.

Cada test usa un PipelineTarget real con una fuente M3U mínima en ``tmp_path``;
sin red. El supervisor **no abre sockets**: ejecuta el pipeline, guarda el
contenido (M3U + XMLTV) en memoria y el servidor único del demonio pregunta por
``playlist_route``.
"""

from pathlib import Path

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import FilterRule, PipelineTarget

BASE = "http://127.0.0.1:34400"


def _playlist(sup: TargetSupervisor, target_id: str = "t0") -> str:
    route = sup.playlist_route(f"/weteve/{target_id}/playlist.m3u")
    assert route is not None, "ruta de playlist no reconocida"
    status, body, _ctype = route
    assert status == 200, body
    assert isinstance(body, str)  # la playlist es texto; el logo es lo binario
    return body


def _write_m3u(path: Path, names: list[str]) -> str:
    lines = ["#EXTM3U"]
    for i, name in enumerate(names):
        lines.append(
            f'#EXTINF:-1 tvg-id="ch{i}" group-title="G{i%2}",{name}\n'
            f"http://stream.example/{i}.ts"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _target(m3u: str, name: str = "t") -> PipelineTarget:
    return PipelineTarget(
        name=name,
        source_index=m3u,
        rules=(FilterRule(field="group-title", pattern="{G0}"),),
    )


class TestTargetSupervisor:
    def test_start_and_serve_playlist(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor(base_url=BASE)
        try:
            assert sup.start_target("t0", _target(m3u))
            info = sup.get_status()["targets"]["t0"]
            assert info["running"] is True
            assert info["playlist_url"] == f"{BASE}/weteve/t0/playlist.m3u"
            body = _playlist(sup)
            assert "Canal A" in body   # G0 pasa el filtro {G0}
            assert "Canal B" not in body
        finally:
            sup.stop_all()

    def test_alias_de_playlist_para_el_target_0(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor(base_url=BASE)
        try:
            sup.start_target("0", _target(m3u))
            canonico = sup.playlist_route("/weteve/0/playlist.m3u")
            corto = sup.playlist_route("/weteve/playlist.m3u")
            heredado = sup.playlist_route("/playlist.m3u")
            assert canonico is not None and corto is not None
            assert heredado is not None
            assert canonico == corto == heredado
            assert canonico[0] == 200 and canonico[2] == "audio/x-mpegurl"
        finally:
            sup.stop_all()

    def test_playlist_de_target_inexistente_404(self) -> None:
        sup = TargetSupervisor(base_url=BASE)
        ruta = sup.playlist_route("/weteve/9/playlist.m3u")
        assert ruta is not None
        status, _body, _ctype = ruta
        assert status == 404

    def test_ruta_que_no_es_playlist(self) -> None:
        sup = TargetSupervisor(base_url=BASE)
        assert sup.playlist_route("/api/v1/status") is None
        assert sup.playlist_route("/weteve/0/otra.xml") is None

    def test_start_twice_replaces_content(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor(base_url=BASE)
        try:
            assert sup.start_target("t0", _target(m3u))
            assert sup.start_target("t0", _target(m3u, name="t0-v2"))
            assert sup.get_status()["targets"]["t0"]["name"] == "t0-v2"
        finally:
            sup.stop_all()

    def test_stop_target(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor(base_url=BASE)
        try:
            sup.start_target("t0", _target(m3u))
            assert sup.stop_target("t0") is True
            assert sup.get_status()["targets"]["t0"]["running"] is False
            parada = sup.playlist_route("/weteve/t0/playlist.m3u")
            assert parada is not None and parada[0] == 404
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
        sup = TargetSupervisor(base_url=BASE)
        try:
            sup.start_target("t0", _target(m3u))
            _write_m3u(m3u_path, ["Canal A", "Canal B", "Canal C"])
            assert sup.reload_target("t0") is True
            # el contenido servido se actualizó (el target ya se servía):
            # Canal C es índice 2, así que su grupo es G0 y pasa el filtro
            assert "Canal C" in _playlist(sup)
        finally:
            sup.stop_all()

    def test_reload_without_start_creates_it(self, tmp_path: Path) -> None:
        """Reload sobre un target no arrancado: construye cache pero no sirve."""
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A"])
        sup = TargetSupervisor(base_url=BASE)
        try:
            assert sup.reload_target("t0", _target(m3u)) is True
            assert sup.reload_target("t0") is True
            info = sup.get_status()["targets"]["t0"]
            assert info["running"] is False
            assert info["channel_count"] == 1
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
            assert sup.start_target("bad", _target(m3u)) is False
            st = sup.get_status()["targets"]["bad"]
            assert "EPG inaccesible" in st["error"]
        finally:
            sup.stop_all()

    def test_list_tv_channels(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            sup.reload_target("t0", _target(m3u))
            # el fixture filtra con {G0}: solo pasa Canal A (G0)
            page = sup.list_tv_channels("t0", limit=10)
            assert page["total"] == 1 and page["count"] == 1
            assert page["channels"][0]["name"] == "Canal A"
            all_un = sup.list_tv_channels("t0", unmatched_only=True)
            assert all_un["total"] == 1
            err = sup.list_epg_channels("t0")
            assert "error" in err
        finally:
            sup.stop_all()

    def test_run_pipeline_metrics(self, tmp_path: Path) -> None:
        m3u = _write_m3u(tmp_path / "src.m3u", ["Canal A", "Canal B"])
        sup = TargetSupervisor()
        try:
            sup.reload_target("t0", _target(m3u))
            metrics = sup.run_pipeline("t0")
            assert metrics["total_input"] == 2
            assert metrics["filtered_count"] == 1
            assert metrics["unmatched_count"] == 1
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
        )
        sup = TargetSupervisor(base_url=BASE)
        try:
            assert sup.start_target("t0", t)
            body = _playlist(sup)
            assert ",Canal A" in body   # sin prefijo por lista
            assert ",Canal B" in body
            assert "A.Canal" not in body and "B.Canal" not in body
        finally:
            sup.stop_all()


class TestDuplicadosDeCalidad:
    """El daemon marca en la tabla qué fila se sirve y cuál es duplicada."""

    def _supervisor_con_duplicado(self):
        from src.application.services.list_manager.models import MappedChannel
        from src.application.services.list_manager.pipeline import PipelineResult

        servido = MappedChannel("LA 1 FULL HD", "http://x/fhd",
                                matched_epg_id="La 1 HD", chno=1)
        duplicado = MappedChannel("LA 1 HD", "http://x/hd",
                                  matched_epg_id="La 1 HD")
        sup = TargetSupervisor()
        sup._results["t0"] = PipelineResult(
            [servido], 2, [], excluded_duplicate=[duplicado])
        return sup

    def test_el_duplicado_se_lista_como_no_servido(self):
        sup = self._supervisor_con_duplicado()
        page = sup.mapping_candidates("t0", include_served=True)
        filas = {c["url"]: c for c in page["channels"]}
        assert filas["http://x/fhd"]["served"] is True
        assert filas["http://x/fhd"]["duplicate"] is False
        assert filas["http://x/hd"]["served"] is False
        assert filas["http://x/hd"]["duplicate"] is True

    def test_el_duplicado_sale_tambien_sin_pedir_servidos(self):
        sup = self._supervisor_con_duplicado()
        page = sup.mapping_candidates("t0", include_served=False)
        assert [c["url"] for c in page["channels"]] == ["http://x/hd"]
        assert page["total"] == 1

    def test_las_metricas_cuentan_los_duplicados(self):
        sup = self._supervisor_con_duplicado()
        assert sup.metrics("t0")["duplicates_count"] == 1
