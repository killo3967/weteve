"""Muestreo de la línea del proveedor: «N de M streams responden».

La lista murió una vez por una rotación de host y nadie se enteró hasta que el
reproductor dejó de verse. El sondeo es barato a propósito: unos pocos canales
repartidos, con caché y sin bloquear ``/status``.
"""

from pathlib import Path

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.list_manager.models import FilterRule, PipelineTarget
from src.application.services.list_manager.stream_probe import (
    StreamProbe,
    probe_url,
    sample_urls,
)

BASE = "http://127.0.0.1:34400"


class TestMuestreo:
    def test_coge_pocas_y_repartidas(self):
        urls = [f"http://x/{i}.ts" for i in range(30)]
        elegidas = sample_urls(urls, 3)
        assert len(elegidas) == 3
        assert elegidas[0] == "http://x/0.ts"
        assert elegidas[-1] != urls[-1], "no son las tres primeras de la lista"

    def test_si_hay_menos_canales_que_muestras_los_coge_todos(self):
        urls = ["http://x/a.ts", "http://x/b.ts"]
        assert sample_urls(urls, 3) == urls

    def test_ignora_urls_vacias(self):
        assert sample_urls(["", "http://x/a.ts", ""], 3) == ["http://x/a.ts"]


class TestSondeo:
    def _probe(self, respuestas: dict[str, bool]) -> StreamProbe:
        return StreamProbe(checker=lambda url: respuestas.get(url, False))

    def test_cuenta_los_que_responden(self):
        probe = self._probe({"http://x/0.ts": True, "http://x/1.ts": False})
        estado = probe.status("0", ["http://x/0.ts", "http://x/1.ts"])
        assert estado["checked"] == 2 and estado["ok"] == 1

    def test_sin_urls_no_sondea(self):
        probe = self._probe({})
        assert probe.status("0", []) == {"checked": 0, "ok": 0,
                                         "pending": False}

    def test_el_resultado_se_cachea(self):
        llamadas: list[str] = []

        def checker(url: str) -> bool:
            llamadas.append(url)
            return True

        probe = StreamProbe(checker=checker, ttl=60.0)
        probe.status("0", ["http://x/a.ts"])
        probe.status("0", ["http://x/a.ts"])
        assert len(llamadas) == 1, "dentro del TTL no se vuelve a sondear"

    def test_pasado_el_ttl_vuelve_a_sondear(self):
        llamadas: list[str] = []

        def checker(url: str) -> bool:
            llamadas.append(url)
            return True

        probe = StreamProbe(checker=checker, ttl=0.0)
        probe.status("0", ["http://x/a.ts"])
        probe.status("0", ["http://x/a.ts"])
        assert len(llamadas) == 2

    def test_un_fallo_no_se_propaga(self):
        def checker(_url: str) -> bool:
            raise RuntimeError("boom")

        estado = StreamProbe(checker=checker).status("0", ["http://x/a.ts"])
        assert estado["checked"] == 0 and estado["pending"] is False

    def test_url_sin_respuesta_es_false(self):
        # ``probe_url`` no lanza: sin red, el sondeo tiene que devolver False
        assert probe_url("") is False


class TestEstadoDelPerfil:
    """``/status`` publica el muestreo junto al resto del estado."""

    def _supervisor(self, tmp_path: Path, probe: StreamProbe) -> TargetSupervisor:
        m3u = tmp_path / "src.m3u"
        m3u.write_text(
            '#EXTM3U\n#EXTINF:-1 tvg-id="ch0" group-title="G0",Canal A\n'
            "http://stream.example/0.ts\n",
            encoding="utf-8")
        sup = TargetSupervisor(base_url=BASE, stream_probe=probe)
        sup.start_target("t0", PipelineTarget(
            name="t", source_index=str(m3u),
            rules=(FilterRule(field="group-title", pattern="{G0}"),)))
        return sup

    def test_publica_cuantos_responden(self, tmp_path: Path):
        probe = StreamProbe(checker=lambda _url: True)
        sup = self._supervisor(tmp_path, probe)
        try:
            streams = sup.get_status()["targets"]["t0"]["streams"]
            assert streams["checked"] == 1 and streams["ok"] == 1
        finally:
            sup.stop_all()

    def test_avisa_si_no_responde_ninguno(self, tmp_path: Path):
        probe = StreamProbe(checker=lambda _url: False)
        sup = self._supervisor(tmp_path, probe)
        try:
            streams = sup.get_status()["targets"]["t0"]["streams"]
            assert streams["checked"] == 1 and streams["ok"] == 0
        finally:
            sup.stop_all()


class TestHiloDeSondeo:
    """El sondeo real corre en un hilo; su cuerpo debe poder guardar solo."""

    def test_el_trabajador_guarda_el_resultado(self):
        probe = StreamProbe(checker=lambda _url: True)
        probe._run("0", ["http://x/a.ts"])
        assert probe.status("0", ["http://x/a.ts"])["ok"] == 1

    def test_un_fallo_del_trabajador_deja_el_estado_vacio(self):
        def checker(_url: str) -> bool:
            raise RuntimeError("boom")

        probe = StreamProbe(checker=checker)
        probe._run("0", ["http://x/a.ts"])
        estado = probe.status("0", ["http://x/a.ts"])
        assert estado["checked"] == 0 and estado["pending"] is False
