"""Numeración por dial real y logo de la fuente de guía.

La lista servida tiene que parecerse a la parrilla del proveedor: el número
sale del dial cuando la guía lo publica (con sus huecos) y el logo, del que
está usando el proveedor ahora mismo.
"""

from datetime import datetime, timedelta

from src.application.services.list_manager.models import (
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel
from src.domain.entities.epg import ChannelInfo, EPGData, Program

AHORA = datetime(2026, 10, 19, 12)


class _FakeM3URepo:
    def __init__(self, channels: list[Channel]):
        self._channels = channels

    def get_channels(self, source: str) -> list[Channel]:
        return list(self._channels)


class _FakeEpgRepo:
    def __init__(self, by_location: dict[str, EPGData]):
        self._by = by_location

    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        return self._by.get(source, EPGData())


def _epg_con_diales(diales: dict[str, int | None], logos: dict[str, str | None],
                    ) -> EPGData:
    """Guía mínima: un programa por canal y los metadatos de canal dados."""
    programs = [
        Program(f"Prog {cid}", AHORA, AHORA + timedelta(hours=1), cid)
        for cid in diales
    ]
    names = {cid: cid.upper() for cid in diales}
    info = {cid: ChannelInfo(dial=diales[cid], logo=logos.get(cid))
            for cid in diales}
    return EPGData(programs=programs, channel_names=names, channel_info=info)


def _run(channels: list[Channel], epg: EPGData, **target_kwargs):
    pipeline = ListManagerPipeline(
        m3u_repository=_FakeM3URepo(channels),
        epg_repository=_FakeEpgRepo({"fake.xml": epg}),
    )
    pipeline.register_source(0, "fake.m3u", "fake.xml")
    target = PipelineTarget(name="t", source_index=0, epg_source_index=0,
                            **target_kwargs)
    return pipeline.run(target)


def _canales() -> list[Channel]:
    # Los nombres casan con los channel-id de la guía por nombre normalizado.
    return [
        Channel("LA 1", "http://s/la1", "General", tvg_id="TVE"),
        Channel("LA 2", "http://s/la2", "General", tvg_id="LA2"),
        Channel("Antena 3", "http://s/a3", "General", tvg_id="A3"),
        Channel("Canal Local", "http://s/local", "General", tvg_id="LOCAL"),
    ]


class TestNumeracionPorDial:
    def test_chno_es_el_dial_de_la_guia(self):
        epg = _epg_con_diales({"TVE": 1, "LA2": 2, "A3": 3, "LOCAL": 7}, {})
        result = _run(_canales(), epg)
        assert [c.chno for c in result.channels] == [1, 2, 3, 7]

    def test_canal_sin_dial_coge_el_siguiente_hueco_libre(self):
        # El dial 2 está ocupado, así que el canal sin dial no puede caer ahí.
        epg = _epg_con_diales({"TVE": 1, "LA2": 2, "A3": 3, "LOCAL": None}, {})
        result = _run(_canales(), epg, starting_number=1)
        by_name = {c.name: c.chno for c in result.channels}
        assert by_name["LA 1"] == 1
        assert by_name["LA 2"] == 2
        assert by_name["Antena 3"] == 3
        assert by_name["Canal Local"] == 4

    def test_la_lista_sale_ordenada_por_dial(self):
        channels = [
            Channel("Antena 3", "http://s/a3", "General", tvg_id="A3"),
            Channel("LA 1", "http://s/la1", "General", tvg_id="TVE"),
            Channel("LA 2", "http://s/la2", "General", tvg_id="LA2"),
        ]
        epg = _epg_con_diales({"TVE": 1, "LA2": 2, "A3": 3}, {})
        result = _run(channels, epg)
        assert [c.name for c in result.channels] == ["LA 1", "LA 2", "Antena 3"]

    def test_dial_repetido_no_duplica_el_numero(self):
        channels = [
            Channel("LA 1", "http://s/la1", "General", tvg_id="TVE"),
            Channel("La Primera", "http://s/primera", "General"),
        ]
        epg = _epg_con_diales({"TVE": 1, "LA2": 2}, {})
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(channels),
            epg_repository=_FakeEpgRepo({"fake.xml": epg}),
        )
        pipeline.register_source(0, "fake.m3u", "fake.xml")
        # Los dos canales apuntan a mano al mismo id de guía (dial 1).
        target = PipelineTarget(name="t", source_index=0, epg_source_index=0,
                                starting_number=1,
                                epg_overrides={"http://s/primera": "TVE"})
        result = pipeline.run(target)
        numeros = [c.chno for c in result.channels]
        assert sorted(numeros) == [1, 2]

    def test_sin_metadatos_numera_igual_que_antes(self):
        # Guía XMLTV normal: sin dial ni logo, numeración 1..N en orden de lista.
        epg = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1), cid)
                      for cid in ("TVE", "LA2", "A3", "LOCAL")],
            channel_names={cid: cid for cid in ("TVE", "LA2", "A3", "LOCAL")},
        )
        result = _run(_canales(), epg, starting_number=1)
        assert [c.chno for c in result.channels] == [1, 2, 3, 4]


class TestLogoPorCapas:
    def test_el_logo_de_la_guia_gana_al_del_m3u(self):
        channels = [Channel("LA 1", "http://s/la1", "General",
                            logo_url="http://m3u/la1.png", tvg_id="TVE")]
        epg = _epg_con_diales({"TVE": 1}, {"TVE": "http://guia/la1.png"})
        result = _run(channels, epg)
        assert result.channels[0].logo_url == "http://guia/la1.png"

    def test_sin_logo_en_la_guia_se_mantiene_el_del_m3u(self):
        channels = [Channel("LA 1", "http://s/la1", "General",
                            logo_url="http://m3u/la1.png", tvg_id="TVE")]
        epg = _epg_con_diales({"TVE": 1}, {"TVE": None})
        result = _run(channels, epg)
        assert result.channels[0].logo_url == "http://m3u/la1.png"


class TestMergeDeMetadatos:
    def test_dos_guias_se_complementan(self):
        """Una guía trae el dial y la otra el logo: el canal se queda con ambos."""
        solo_dial = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1), "TVE")],
            channel_names={"TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(dial=1)},
        )
        solo_logo = EPGData(
            programs=[Program("Q", AHORA, AHORA + timedelta(hours=2), "TVE")],
            channel_names={"TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(logo="http://guia/la1.png")},
        )
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo([Channel("LA 1", "http://s/la1", "G",
                                                 tvg_id="TVE")]),
            epg_repository=_FakeEpgRepo({"a.xml": solo_dial, "b.xml": solo_logo}),
        )
        pipeline.register_merged(
            "merged",
            (SourceRef("s1", "lista", "lista.m3u"),),
            (SourceRef("e1", "guia-a", "a.xml"), SourceRef("e2", "guia-b", "b.xml")),
        )
        target = PipelineTarget(name="t", source_index="merged",
                                source_refs=(SourceRef("s1", "lista", "lista.m3u"),),
                                epg_refs=(SourceRef("e1", "guia-a", "a.xml"),
                                          SourceRef("e2", "guia-b", "b.xml")))
        result = pipeline.run(target)
        canal = result.channels[0]
        assert canal.chno == 1
        assert canal.logo_url == "http://guia/la1.png"


class TestPrioridadDeGuias:
    """Con dos guías que anuncian el mismo canal, la prioridad decide."""

    def test_la_guia_de_mas_prioridad_aporta_el_id_y_el_dial(self):
        # La guía general anuncia el nombre exacto del canal; la de Movistar,
        # sin la etiqueta de calidad pero con dial y logo.
        general = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1), "doble.la1")],
            channel_names={"doble.la1": "La 1 HD"},
        )
        movistar = EPGData(
            programs=[Program("Q", AHORA, AHORA + timedelta(hours=1), "TVE")],
            channel_names={"TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(dial=1, logo="http://l/tve.png")},
        )
        ref_general = SourceRef("g", "guía general", "general.xml", 0)
        ref_movistar = SourceRef("m", "Movistar+", "movistar.xml", 10)
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(
                [Channel("LA 1 HD", "http://s/la1", "General")]),
            epg_repository=_FakeEpgRepo({"general.xml": general,
                                         "movistar.xml": movistar}),
        )
        pipeline.register_merged("m", (SourceRef("s", "lista", "l.m3u"),),
                                 (ref_general, ref_movistar))
        target = PipelineTarget(
            name="t", source_index="m",
            source_refs=(SourceRef("s", "lista", "l.m3u"),),
            epg_refs=(ref_general, ref_movistar))
        canal = pipeline.run(target).channels[0]
        assert canal.matched_epg_id == "TVE"
        assert canal.chno == 1
        assert canal.logo_url == "http://l/tve.png"

    def test_sin_prioridades_gana_la_primera_de_la_cartera(self):
        general = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1), "doble.la1")],
            channel_names={"doble.la1": "La 1 HD"},
        )
        movistar = EPGData(
            programs=[Program("Q", AHORA, AHORA + timedelta(hours=1), "TVE")],
            channel_names={"TVE": "LA 1"},
        )
        ref_general = SourceRef("g", "guía general", "general.xml")
        ref_movistar = SourceRef("m", "Movistar+", "movistar.xml")
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(
                [Channel("LA 1 HD", "http://s/la1", "General")]),
            epg_repository=_FakeEpgRepo({"general.xml": general,
                                         "movistar.xml": movistar}),
        )
        pipeline.register_merged("m", (SourceRef("s", "lista", "l.m3u"),),
                                 (ref_general, ref_movistar))
        target = PipelineTarget(
            name="t", source_index="m",
            source_refs=(SourceRef("s", "lista", "l.m3u"),),
            epg_refs=(ref_general, ref_movistar))
        assert pipeline.run(target).channels[0].matched_epg_id == "doble.la1"


class TestMetadatosPorNombre:
    """El dial y el logo no dependen de a qué guía apunte el mapeo manual."""

    def test_mapeo_manual_a_otra_guia_sigue_recibiendo_dial_y_logo(self):
        # El canal está mapeado a mano al id de la guía general ("La 1 HD"),
        # que no publica metadatos; Movistar+ lo anuncia como "LA 1".
        general = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1),
                              "doble.la1")],
            channel_names={"doble.la1": "La 1 HD"},
        )
        movistar = EPGData(
            programs=[Program("Q", AHORA, AHORA + timedelta(hours=1), "TVE")],
            channel_names={"TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(dial=1, logo="http://l/tve.png")},
        )
        ref_general = SourceRef("g", "guía general", "general.xml")
        ref_movistar = SourceRef("m", "Movistar+", "movistar.xml", 10)
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(
                [Channel("LA 1 HD", "http://s/la1", "General")]),
            epg_repository=_FakeEpgRepo({"general.xml": general,
                                         "movistar.xml": movistar}),
        )
        pipeline.register_merged("m", (SourceRef("s", "lista", "l.m3u"),),
                                 (ref_general, ref_movistar))
        target = PipelineTarget(
            name="t", source_index="m",
            source_refs=(SourceRef("s", "lista", "l.m3u"),),
            epg_refs=(ref_general, ref_movistar),
            epg_overrides={"http://s/la1": "doble.la1"})
        canal = pipeline.run(target).channels[0]
        # la guía del mapeo sigue mandando en la parrilla…
        assert canal.matched_epg_id == "doble.la1"
        # …pero el número y el logo vienen de quien los publica
        assert canal.chno == 1
        assert canal.logo_url == "http://l/tve.png"


class TestDialYLogoDeGuiasDistintas:
    """El dial viene de una guía y el logo de otra: el canal se queda con los dos."""

    def test_se_combinan_por_nombre(self):
        movistar = EPGData(
            programs=[Program("P", AHORA, AHORA + timedelta(hours=1), "TVE")],
            channel_names={"TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(dial=1)},
        )
        koala = EPGData(
            programs=[Program("Q", AHORA, AHORA + timedelta(hours=1), "k.la1")],
            channel_names={"k.la1": "La 1 HD"},
            channel_info={"k.la1": ChannelInfo(logo="http://koala/la1.png")},
        )
        ref_mv = SourceRef("m", "Movistar+", "movistar.xml", 10)
        ref_koala = SourceRef("k", "Koala", "koala.xml", 0)
        pipeline = ListManagerPipeline(
            m3u_repository=_FakeM3URepo(
                [Channel("LA 1 HD", "http://s/la1", "General")]),
            epg_repository=_FakeEpgRepo({"movistar.xml": movistar,
                                         "koala.xml": koala}),
        )
        pipeline.register_merged("m", (SourceRef("s", "lista", "l.m3u"),),
                                 (ref_mv, ref_koala))
        target = PipelineTarget(
            name="t", source_index="m",
            source_refs=(SourceRef("s", "lista", "l.m3u"),),
            epg_refs=(ref_mv, ref_koala))
        canal = pipeline.run(target).channels[0]
        assert canal.chno == 1
        assert canal.logo_url == "http://koala/la1.png"
