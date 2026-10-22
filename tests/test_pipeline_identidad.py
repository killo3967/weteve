"""Identidad del canal: dial y agrupación aunque la parrilla venga de otra guía.

Cada guía bautiza al mismo canal a su manera. El pipeline tiene que servir una
sola variante, con el dial real y con los programas que haya elegido el mapeo,
que puede apuntar a una guía sin dial (guia-2, guia-1).
"""

from datetime import datetime, timedelta

from src.application.services.list_manager.models import PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel
from src.domain.entities.epg import ChannelInfo, EPGData, Program

AHORA = datetime(2026, 10, 20, 12)
KOALA = "M+ LALIGA TV HD"      # id de la guía que no publica dial
DOBLE = "M+ LaLiga HD"


class _FakeM3URepo:
    def __init__(self, canales: list[Channel]):
        self._canales = canales

    def get_channels(self, source: str) -> list[Channel]:
        return list(self._canales)


class _FakeEpgRepo:
    def __init__(self, por_ruta: dict[str, EPGData]):
        self._por_ruta = por_ruta

    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        return self._por_ruta.get(source, EPGData())


def _guia() -> EPGData:
    """Tres guías en una: Movistar+ (con dial), guia-2 y guia-1 (sin dial)."""
    nombres = {
        "MLIGA": "M+ LALIGA",
        "MLIG1": "M+ LALIGA 2",
        "MPLUS": "Movistar Plus",
        KOALA: "M+ LALIGA TV HD",
        DOBLE: "M+ LaLiga",
        "Canal Sur HD": "Canal Sur HD",
    }
    info = {
        "MLIGA": ChannelInfo(dial=54, logo="http://l/mliga.png"),
        "MLIG1": ChannelInfo(dial=57, logo="http://l/mlig1.png"),
        "MPLUS": ChannelInfo(dial=7, logo="http://l/mplus.png"),
    }
    prioridad = {"MLIGA": 10, "MLIG1": 10, "MPLUS": 10, KOALA: 0, DOBLE: 0,
                 "Canal Sur HD": 5}
    programas = [Program("P", AHORA, AHORA + timedelta(hours=1), cid)
                 for cid in nombres]
    return EPGData(programs=programas, channel_names=nombres,
                   channel_info=info, channel_priority=prioridad)


def _canales() -> list[Channel]:
    return [
        Channel("M+ LaLigaTVHD", "http://x/1", "SPAIN"),
        Channel("M+ LaLigaTVHD+", "http://x/2", "SPAIN"),
        Channel("M+ LaLigaTVSD", "http://x/3", "SPAIN"),
        Channel("M+ LaLigaTV FULL HD", "http://x/4", "SPAIN"),
        Channel("M+ LaLigaTV 2 HD", "http://x/5", "SPAIN"),
        Channel("CANAL SUR HD", "http://x/6", "SPAIN"),
    ]


def _run(**kwargs):
    pipeline = ListManagerPipeline(
        m3u_repository=_FakeM3URepo(_canales()),
        epg_repository=_FakeEpgRepo({"guia.xml": _guia()}))
    pipeline.register_source(0, "lista.m3u", "guia.xml")
    target = PipelineTarget(name="t", source_index=0, epg_source_index=0,
                            **kwargs)
    return pipeline.run(target)


class TestUnaVariantePorCanal:
    def test_las_cuatro_variantes_son_una(self):
        result = _run()
        # Las cuatro se sirven una vez, con el nombre de la guía de Movistar.
        assert [c.name for c in result.channels] == ["M+ LALIGA",
                                                    "M+ LALIGA 2",
                                                    "CANAL SUR HD"]
        assert len(result.excluded_duplicate) == 3

    def test_la_lista_sale_ordenada_por_dial(self):
        result = _run()
        assert [c.chno for c in result.channels] == [54, 57, 58]


class TestDialEIdentidad:
    def test_sin_mapeo_manual_parrilla_y_dial_vienen_de_movistar(self):
        result = _run()
        canal = next(c for c in result.channels if c.canonical_id == "MLIGA")
        assert canal.matched_epg_id == "MLIGA"
        assert canal.chno == 54
        assert canal.logo_url == "http://l/mliga.png"

    def test_un_mapeo_a_koala_no_quita_el_dial(self):
        """El mapeo manual manda para la parrilla, no para la identidad."""
        overrides = {f"http://x/{i}": KOALA for i in (1, 2, 3, 4)}
        result = _run(epg_overrides=overrides)
        canal = next(c for c in result.channels if c.canonical_id == "MLIGA")
        assert canal.matched_epg_id == KOALA
        assert canal.chno == 54
        assert canal.logo_url == "http://l/mliga.png"

    def test_el_canal_2_no_se_queda_el_numero_del_1(self):
        result = _run()
        canal = next(c for c in result.channels if c.canonical_id == "MLIG1")
        assert canal.chno == 57
        assert canal.name == "M+ LALIGA 2"

    def test_sin_pareja_en_la_guia_se_numera_al_final(self):
        result = _run()
        canal = next(c for c in result.channels if c.name == "CANAL SUR HD")
        assert canal.canonical_id is None
        assert canal.chno == 58

    def test_una_guia_sin_metadatos_no_inventa_diales(self):
        """Un target con guías sin dial sigue numerando en secuencia."""
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(_canales()),
            epg_repository=_FakeEpgRepo({"koala.xml": EPGData(
                programs=[Program("P", AHORA, AHORA + timedelta(hours=1),
                                  KOALA)],
                channel_names={KOALA: "M+ LALIGA TV HD"},
            )}))
        pipeline.register_source(0, "lista.m3u", "koala.xml")
        target = PipelineTarget(name="t", source_index=0, epg_source_index=0,
                                epg_overrides={f"http://x/{i}": KOALA
                                               for i in range(1, 7)})
        result = pipeline.run(target)
        assert all(c.canonical_id is None for c in result.channels)
        assert [c.chno for c in result.channels] == [1, 2, 3]
