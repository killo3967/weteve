"""Tests del pipeline y de los builders del gestor de listas."""

from datetime import datetime
from pathlib import Path

from src.application.services.list_manager.models import FilterRule, MappedChannel, PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.application.services.list_manager.playlist_builder import (
    build_m3u,
    build_xmltv,
    epg_id_of,
)
from src.domain.entities.channel import Channel
from src.domain.entities.epg import EPGData, Program
from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

FIXTURES = Path(__file__).parent / "fixtures" / "list_manager"


class _FakeM3URepo:
    """Repositorio M3U fijo para tests sin red ni disco."""

    def __init__(self, channels: list[Channel]):
        self._channels = channels

    def get_channels(self, source: str) -> list[Channel]:
        return list(self._channels)


class _FakeEpgRepo(XMLTVRepository):
    """Repositorio EPG fijo para tests."""

    def __init__(self, epg: EPGData | None = None):
        self._epg = epg

    def load_epg(self, source: str) -> EPGData:
        return self._epg or EPGData()


def _channels() -> list[Channel]:
    return [
        Channel("La 1 HD", "http://s/la1", "General", tvg_id="es.la1"),
        Channel("Teledeporte", "http://s/tdp", "Deportes"),
        Channel("Canal XXX", "http://s/xxx1", "Adultos"),
        Channel("Canal Cine Plus", "http://s/cine", "Cine"),
    ]


def _epg() -> EPGData:
    programs = [
        Program("Noticias 1", datetime(2026, 10, 8, 12, 0), datetime(2026, 10, 8, 13, 0), "es.la1"),
        Program("Deportes TV", datetime(2026, 10, 8, 13, 0), datetime(2026, 10, 8, 14, 0), "es.tdp"),
    ]
    return EPGData(
        programs=programs,
        channel_names={"es.la1": "La 1", "es.tdp": "Teledeporte"},
    )


def _pipeline(epg: EPGData | None = None, channels: list[Channel] | None = None) -> ListManagerPipeline:
    p = ListManagerPipeline(
        m3u_repository=_FakeM3URepo(channels or _channels()),
        epg_repository=_FakeEpgRepo(epg),
    )
    p.register_source(0, "fake.m3u", "fake.xml" if epg is not None else None)
    return p


def _target(**kwargs) -> PipelineTarget:
    defaults = {"name": "test", "source_index": 0, "epg_source_index": 0}
    defaults.update(kwargs)
    return PipelineTarget(**defaults)


class TestPipelineFilters:
    def test_sin_filtros_pasa_todo(self):
        result = _pipeline(epg=_epg()).run(_target())
        assert result.total_input == 4
        # La EPG del helper solo tiene 'La 1' y 'Teledeporte': la regla de
        # servidor descarta Canal XXX y Canal Cine Plus por no tener guía
        # asignada (ver tests/test_no_epg_excluded.py).
        assert result.filtered_count == 2

    def test_filtro_excluye_adultos(self):
        target = _target(rules=(FilterRule("custom-filter", "!{XXX}"),))
        result = _pipeline(epg=_epg()).run(target)
        names = [c.name for c in result.channels]
        assert "Canal XXX" not in names
        # 4 pasan a la fase de EPG tras el filtro, y 'Canal Cine Plus' cae por
        # no tener guía asignada.
        assert result.filtered_count == 2

    def test_filtro_grupo(self):
        target = _target(rules=(FilterRule("group-title", "Deportes"),))
        result = _pipeline(epg=_epg()).run(target)
        assert [c.name for c in result.channels] == ["Teledeporte"]


class TestPipelineEpg:
    def test_match_por_tvg_id(self):
        result = _pipeline(epg=_epg()).run(_target())
        by_name = {c.name: c for c in result.channels}
        assert by_name["La 1 HD"].matched_epg_id == "es.la1"
        assert by_name["Teledeporte"].matched_epg_id == "es.tdp"

    def test_match_por_nombre_fallback(self):
        # Canal sin tvg-id pero con nombre que coincide con el XMLTV
        channels = [Channel("Teledeporte", "http://s/tdp", "Deportes")]
        result = _pipeline(epg=_epg(), channels=channels).run(_target())
        assert result.channels[0].matched_epg_id == "es.tdp"
        assert result.unmatched == []

    def test_sin_match_va_a_unmatched(self):
        channels = [Channel("Canal Raro", "http://s/raro", "X")]
        result = _pipeline(epg=_epg(), channels=channels).run(_target())
        assert result.unmatched == ["Canal Raro"]
        # Sin guía no se sirve: el canal se reporta en excluded_no_epg, que es
        # de donde la tabla de mapeo saca la fila para asignarle Dummy 1h.
        assert result.channels == []
        assert [c.name for c in result.excluded_no_epg] == ["Canal Raro"]

    def test_override_tiene_prioridad(self):
        result = _pipeline(epg=_epg()).run(_target(epg_overrides={"http://s/la1": "es.tdp"}))
        by_url = {c.url: c for c in result.channels}
        assert by_url["http://s/la1"].matched_epg_id == "es.tdp"

    def test_sin_epg_todo_unmatched(self):
        result = _pipeline(epg=None).run(_target())
        assert len(result.unmatched) == 4

    def test_numeracion_secuencial(self):
        result = _pipeline(epg=_epg()).run(_target(starting_number=100))
        assert result.channels[0].chno == 100
        assert result.channels[1].chno == 101


class TestEpgIdOf:
    def test_prefiere_la_asignacion_manual(self):
        ch = MappedChannel("La 1", "http://s/la1", tvg_id="propio",
                           matched_epg_id="manual")
        assert epg_id_of(ch) == "manual"

    def test_cae_al_tvg_id_propio(self):
        ch = MappedChannel("La 1", "http://s/la1", tvg_id="es.la1")
        assert epg_id_of(ch) == "es.la1"

    def test_sin_ningun_id_devuelve_none(self):
        assert epg_id_of(MappedChannel("La 1", "http://s/la1")) is None


class TestBuildM3U:
    def test_contenido_minimo(self):
        ch = MappedChannel("La 1", "http://s/la1", "General",
                           tvg_id="es.la1", matched_epg_id="es.la1", chno=1)
        out = build_m3u([ch])
        assert out.startswith("#EXTM3U")
        assert 'group-title="General"' in out
        assert "http://s/la1" in out

    def test_tvg_id_lleva_el_id_de_la_guia(self):
        """El id EPG es la clave con la que el reproductor enlaza la guía;

        sin él solo queda el emparejamiento por nombre, que la mayoría de
        clientes (TiviMate, Kodi, Jellyfin) no intenta.
        """
        chans = [
            MappedChannel("Raro", "http://s/raro", "X", chno=1),
            MappedChannel("La 1", "http://s/la1", "X", tvg_id="es.la1",
                          matched_epg_id="es.la1", chno=2),
        ]
        out = build_m3u(chans)
        extinf = [l for l in out.splitlines() if l.startswith("#EXTINF")]
        assert len(extinf) == 2
        # el canal sin guía no enlaza nada; el mapeado lleva su channel-id
        assert 'tvg-id=""' in extinf[0]
        assert 'tvg-id="es.la1"' in extinf[1]

    def test_group_title_y_chno_siempre_presentes(self):
        out = build_m3u([MappedChannel("La 1", "http://s/la1", "SPAIN", chno=7)])
        assert 'group-title="SPAIN"' in out
        assert 'tvg-chno="7"' in out

    def test_nombre_sin_prefijo_de_lista(self):
        ch = MappedChannel("LA 1 HD", "http://s/la1", source_name="platero2026",
                           chno=1)
        out = build_m3u([ch])
        assert out.rstrip().endswith("http://s/la1")
        assert ",LA 1 HD" in out
        assert "platero2026" not in out


class TestBuildXmltv:
    def test_solo_canales_con_guia_real(self):
        channels = [
            MappedChannel("La 1", "http://s/la1", matched_epg_id="es.la1"),
            MappedChannel("Sin EPG", "http://s/x"),
        ]
        out = build_xmltv(channels, _epg())
        assert "<channel" in out
        assert "Noticias 1" in out
        # el canal sin id EPG no se declara ni recibe guía inventada
        assert out.count("<channel ") == 1
        assert "Sin EPG" not in out

    def test_sin_epg_repositorio_no_inventa_nada(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(build_xmltv([MappedChannel("La 1", "http://s/la1")], None))
        assert root.findall("channel") == []
        assert root.findall("programme") == []

    def test_usa_el_tvg_id_propio_si_no_hay_match(self):
        import xml.etree.ElementTree as ET

        ch = MappedChannel("La 1", "http://s/la1", tvg_id="es.la1")
        root = ET.fromstring(build_xmltv([ch], _epg()))
        assert [el.get("id") for el in root.findall("channel")] == ["es.la1"]
        assert len(root.findall("programme")) == 1

    def test_el_m3u_y_el_xmltv_usan_el_mismo_id(self):
        """Playlist y guía se enlazan por el mismo channel-id."""
        import re

        channels = [
            MappedChannel("La 1", "http://s/la1", matched_epg_id="es.la1", chno=1),
            MappedChannel("Sin EPG", "http://s/x", chno=2),
        ]
        m3u = build_m3u(channels)
        assert set(re.findall(r'tvg-id="([^"]*)"', m3u)) == {"es.la1", ""}
        assert "es.la1" in build_xmltv(channels, _epg())

    def test_dos_canales_al_mismo_id_no_duplican_canal(self):
        import xml.etree.ElementTree as ET

        chans = [
            MappedChannel("A", "http://s/a", matched_epg_id="es.la1"),
            MappedChannel("B", "http://s/b", matched_epg_id="es.la1"),
        ]
        root = ET.fromstring(build_xmltv(chans, _epg()))
        ids = [el.get("id") for el in root.findall("channel")]
        assert ids.count("es.la1") == 1

    def test_programas_no_se_reptien_por_franja(self):
        import xml.etree.ElementTree as ET

        chans = [
            MappedChannel("A", "http://s/a", matched_epg_id="es.la1"),
            MappedChannel("B", "http://s/b", matched_epg_id="es.la1"),
        ]
        root = ET.fromstring(build_xmltv(chans, _epg()))
        starts = [p.get("start") for p in
                  root.findall(".//programme[@channel='es.la1']")]
        assert starts and len(starts) == len(set(starts))

    def test_programas_del_canal_matcheado(self):
        epg = _epg()
        ch = [MappedChannel("Teledeporte", "http://s/tdp", matched_epg_id="es.tdp")]
        out = build_xmltv(ch, epg)
        assert "Partido amistoso" not in out or "Deportes TV" in out
        assert 'channel="es.tdp"' in out

    def test_xml_valido(self):
        import xml.etree.ElementTree as ET

        epg = _epg()
        ch = [MappedChannel("La 1", "http://s/la1", matched_epg_id="es.la1")]
        out = build_xmltv(ch, epg)
        root = ET.fromstring(out)
        assert root.tag == "tv"
        assert root.find("channel") is not None


class TestFixturesReales:
    def test_fixture_m3u_parsea(self):
        """El fixture real se parsea con el repositorio existente."""
        from src.infrastructure.adapters.file_m3u_repository import FileM3URepository

        repo = FileM3URepository()
        channels = repo.get_channels(str(FIXTURES / "sample.m3u"))
        assert len(channels) == 5
        assert channels[0].tvg_id == "es.la1"

    def test_fixture_xmltv_parsea(self):
        repo = XMLTVRepository()
        epg = repo.load_epg(str(FIXTURES / "sample.xmltv"))
        assert epg.get_programs_for_channel("es.la1")

    def test_pipeline_con_fixtures(self):
        from src.infrastructure.adapters.file_m3u_repository import FileM3URepository

        repo = FileM3URepository()
        epg_repo = XMLTVRepository()
        pipeline = ListManagerPipeline(m3u_repository=repo, epg_repository=epg_repo)
        pipeline.register_source(0, str(FIXTURES / "sample.m3u"), str(FIXTURES / "sample.xmltv"))
        target = _target(rules=(FilterRule("custom-filter", "!{XXX}"),))
        result = pipeline.run(target)
        # El fixture trae 5 canales: 1 excluido por el filtro (XXX), 1 sin guía
        # en el XMLTV (Canal Cine Plus -> descartado por la regla de servidor).
        assert result.filtered_count == 3
        names = [c.name for c in result.channels]
        assert "Canal XXX" not in names
        # Teledeporte no trae tvg-id en el fixture -> match por nombre
        tdp = next(c for c in result.channels if c.name == "Teledeporte")
        assert tdp.matched_epg_id == "es.tdp"


class TestDisabledChannels:
    def test_disabled_excluded_from_result(self):
        p = _pipeline(_epg())
        t = _target(disabled_channels=("http://s/tdp", "http://s/xxx1"))
        result = p.run(t)
        urls = [c.url for c in result.channels]
        assert "http://s/tdp" not in urls
        assert "http://s/xxx1" not in urls
        assert "http://s/la1" in urls

    def test_disabled_renumbers_sequence(self):
        """Al excluir canales, la numeración no deja huecos."""
        p = _pipeline(_epg())
        t = _target(disabled_channels=("http://s/la1",))
        result = p.run(t)
        assert [c.chno for c in result.channels] == list(
            range(1, len(result.channels) + 1))

    def test_empty_disabled_no_effect(self):
        p = _pipeline(_epg())
        r1 = p.run(_target())
        r2 = p.run(_target(disabled_channels=()))
        assert len(r1.channels) == len(r2.channels)


class TestVariantesDeCalidad:
    """El mismo canal repetido con etiquetas de calidad se sirve una vez."""

    @staticmethod
    def _variantes():
        return [
            Channel("LA 1 HD", "http://s/la1-hd", "General", tvg_id="es.la1"),
            Channel("LA 1 FULL HD", "http://s/la1-fhd", "General",
                    tvg_id="es.la1"),
            Channel("LA 1 SD", "http://s/la1-sd", "General", tvg_id="es.la1"),
        ]

    def test_se_sirve_la_mejor_variante(self):
        p = _pipeline(epg=_epg(), channels=self._variantes())
        result = p.run(_target())
        assert [c.name for c in result.channels] == ["LA 1 FULL HD"]
        assert result.channels[0].chno == 1        # numeración sin huecos
        assert {c.name for c in result.excluded_duplicate} == {
            "LA 1 HD", "LA 1 SD"}

    def test_desactivar_la_mejor_deja_pasar_la_siguiente(self):
        """El apagado manual va antes: sirve para elegir variante."""
        canales = [
            Channel("LA 1 4K", "http://s/4k", "General", tvg_id="es.la1"),
            Channel("LA 1 HD", "http://s/hd", "General", tvg_id="es.la1"),
        ]
        p = _pipeline(epg=_epg(), channels=canales)
        result = p.run(_target(disabled_channels=("http://s/4k",)))
        assert [c.name for c in result.channels] == ["LA 1 HD"]
        assert result.excluded_duplicate == []

    def test_prioridad_propia_del_target(self):
        """El móvil puede preferir SD: la tabla del target manda."""
        p = _pipeline(epg=_epg(), channels=self._variantes())
        result = p.run(_target(quality_priority={"sd": 5, "hd": 3,
                                                "full hd": 2}))
        assert [c.name for c in result.channels] == ["LA 1 SD"]

    def test_no_deduplica_canales_sin_guia(self):
        canales = [
            Channel("Raro HD", "http://s/raro-hd", "General"),
            Channel("Raro SD", "http://s/raro-sd", "General"),
        ]
        p = _pipeline(epg=_epg(), channels=canales)
        result = p.run(_target())
        # sin guía no llegan a servirse: los descarta la regla de servidor
        assert result.channels == []
        assert len(result.excluded_no_epg) == 2
        assert result.excluded_duplicate == []
