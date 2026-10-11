"""Tests del pipeline y de los builders del gestor de listas."""

from datetime import datetime
from pathlib import Path

from src.application.services.list_manager.models import FilterRule, MappedChannel, PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.application.services.list_manager.playlist_builder import build_m3u, build_xmltv
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
        assert result.filtered_count == 4

    def test_filtro_excluye_adultos(self):
        target = _target(rules=(FilterRule("custom-filter", "!{XXX}"),))
        result = _pipeline(epg=_epg()).run(target)
        names = [c.name for c in result.channels]
        assert "Canal XXX" not in names
        assert result.filtered_count == 3

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
        assert result.channels[0].matched_epg_id is None
        assert result.unmatched == ["Canal Raro"]

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


class TestBuildM3U:
    def test_contenido_minimo(self):
        ch = MappedChannel("La 1", "http://s/la1", "General", tvg_id="es.la1", matched_epg_id="es.la1", chno=1)
        out = build_m3u([ch])
        assert out.startswith("#EXTM3U")
        assert 'tvg-id="es.la1"' in out
        assert 'group-title="General"' in out
        assert "http://s/la1" in out

    def test_sin_epg_deja_tvg_id_vacio(self):
        ch = MappedChannel("Raro", "http://s/raro", "X")
        out = build_m3u([ch])
        assert 'tvg-id=""' in out


class TestBuildXmltv:
    def test_solo_canales_con_match(self):
        epg = _epg()
        channels = [
            MappedChannel("La 1", "http://s/la1", matched_epg_id="es.la1"),
            MappedChannel("Sin EPG", "http://s/x"),
        ]
        out = build_xmltv(channels, epg)
        assert "<channel" in out
        assert "Noticias 1" in out
        # el canal sin EPG no debe aparecer como canal
        assert 'channel="es.x"' not in out

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
        assert result.filtered_count == 4
        names = [c.name for c in result.channels]
        assert "Canal XXX" not in names
        # Teledeporte no trae tvg-id en el fixture -> match por nombre
        tdp = next(c for c in result.channels if c.name == "Teledeporte")
        assert tdp.matched_epg_id == "es.tdp"
