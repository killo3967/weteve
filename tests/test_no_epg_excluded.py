"""Tests de la regla 'sin guía EPG -> canal fuera de la lista'.

Decisión del usuario (2026-10-14): la desactivación vive en el servidor, no
solo en la casilla de la UI. Un canal sin guía (real o dummy) no se sirve.

Consecuencia conocida y aceptada: con una EPG que empareja mal, la lista se
vacía. Por eso el pipeline expone ``excluded_no_epg`` —sin esa lista la tabla
de mapeo no tendría filas que mostrar, y el usuario no podría asignar Dummy 1h
para rescatar un canal: callejón sin salida.
"""

from datetime import datetime

from src.application.services.list_manager.dummy_epg import make_dummy_id
from src.application.services.list_manager.models import (
    FilterRule,
    MappedChannel,
    PipelineTarget,
)
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.application.services.list_manager.playlist_builder import (
    build_dummy_epg_id,
    build_xmltv,
)
from src.domain.entities.channel import Channel
from src.domain.entities.epg import EPGData, Program

GUIDE_URL = "http://s/la1"
NOGUIDE_URL = "http://s/cine"
DUMMY_URL = "http://s/dummy"


def _epg() -> EPGData:
    """EPG que solo tiene guía para La 1: los demás canales quedan sin guía."""
    return EPGData(
        programs=[
            Program("Noticias 1", datetime(2026, 10, 8, 12, 0),
                    datetime(2026, 10, 8, 13, 0), "es.la1"),
        ],
        channel_names={"es.la1": "La 1", "es.otro": "Otro Canal"},
    )


def _channels() -> list[Channel]:
    return [
        Channel("La 1 HD", GUIDE_URL, "General", tvg_id="es.la1"),
        Channel("Canal Cine Plus", NOGUIDE_URL, "Cine"),
    ]


def _pipeline(channels=None, epg=None) -> ListManagerPipeline:
    p = ListManagerPipeline(
        m3u_repository=_FakeM3U(channels if channels is not None else _channels()),
        epg_repository=_FakeEpg(epg if epg is not None else _epg()),
    )
    p.register_source(0, "fake.m3u", "fake.xml")
    return p


class _FakeM3U:
    def __init__(self, channels):
        self._channels = channels

    def get_channels(self, source):
        return list(self._channels)


class _FakeEpg:
    def __init__(self, epg):
        self._epg = epg

    def load_epg(self, source, fresh=False):
        return self._epg


def _target(**kwargs) -> PipelineTarget:
    defaults = {"name": "t", "source_index": 0, "epg_source_index": 0}
    defaults.update(kwargs)
    return PipelineTarget(**defaults)


class TestReglaServidor:
    def test_sin_guia_no_se_sirve(self):
        result = _pipeline().run(_target())
        assert [c.name for c in result.channels] == ["La 1 HD"]

    def test_sin_guia_queda_expuesto_para_la_ui(self):
        """El canal descartado debe seguir visible: es la única forma de
        asignarle Dummy 1h desde la tabla y rescatarlo."""
        result = _pipeline().run(_target())
        assert [c.name for c in result.excluded_no_epg] == ["Canal Cine Plus"]

    def test_descartado_por_filtro_no_aparece_como_sin_guia(self):
        """Un canal excluido por regla de filtro no es un problema de guía:
        no debe poblar la tabla de mapeo."""
        target = _target(rules=(FilterRule("custom-filter", "!{Cine}"),))
        result = _pipeline().run(target)
        assert result.excluded_no_epg == []

    def test_dummy_devuelve_el_canal_a_la_lista(self):
        target = _target(epg_overrides={NOGUIDE_URL: build_dummy_epg_id(
            NOGUIDE_URL)})
        result = _pipeline().run(target)
        names = sorted(c.name for c in result.channels)
        assert names == ["Canal Cine Plus", "La 1 HD"]

    def test_override_real_devuelve_el_canal(self):
        target = _target(epg_overrides={NOGUIDE_URL: "es.otro"})
        result = _pipeline().run(target)
        names = sorted(c.name for c in result.channels)
        assert names == ["Canal Cine Plus", "La 1 HD"]

    def test_sin_fuente_epg_no_hay_regla(self):
        """Sin guía configurada no se puede exigir guía: serviría lista vacía.

        La regla se desactiva sola; 'no hay EPG' es configuración, no un defecto
        del canal.
        """
        p = ListManagerPipeline(
            m3u_repository=_FakeM3U(_channels()), epg_repository=_FakeEpg(None))
        p.register_source(0, "fake.m3u", None)
        result = p.run(_target(epg_source_index=None))
        assert len(result.channels) == 2
        assert result.require_epg is False
        assert len(result.unmatched) == 2

    def test_desactivado_a_mano_gana_a_la_guia(self):
        """Un canal con guía pero desactivado a mano sigue fuera."""
        target = _target(disabled_channels=(GUIDE_URL,))
        result = _pipeline().run(target)
        assert result.channels == []
        assert [c.name for c in result.excluded_disabled] == ["La 1 HD"]

    def test_la_numeracion_no_deja_huecos(self):
        """Los chno se asignan sobre los canales que se sirven: si el
        descarte ocurriera después de numerar, la lista tendría un salto."""
        channels = [
            Channel("La 1 HD", GUIDE_URL, "General", tvg_id="es.la1"),
            Channel("Sin Guia", "http://s/x", "Cine"),
            Channel("Otro Con Guia", "http://s/y", "General", tvg_id="es.la1"),
        ]
        result = _pipeline(channels=channels).run(_target())
        assert [c.name for c in result.channels] == ["La 1 HD", "Otro Con Guia"]
        assert [c.chno for c in result.channels] == [1, 2]

    def test_el_dummy_se_resuelve_por_url_no_por_nombre(self):
        """El override guarda el id derivado de la url. Dos canales con el
        mismo nombre no pueden compartir guía dummy: cada uno la suya."""
        channels = [
            Channel("Repetido", "http://s/a", "Cine"),
            Channel("Repetido", "http://s/b", "Cine"),
        ]
        target = _target(epg_overrides={
            "http://s/a": make_dummy_id("http://s/a", "Repetido"),
            "http://s/b": make_dummy_id("http://s/b", "Repetido"),
        })
        result = _pipeline(channels=channels).run(target)
        ids = {c.matched_epg_id for c in result.channels}
        assert len(ids) == 2
        assert len(result.channels) == 2


class TestDummyEnXmltv:
    def _mapped(self, name, url, epg_id):
        return MappedChannel(name=name, url=url, matched_epg_id=epg_id)

    def test_dummy_genera_channel_con_el_nombre_del_canal(self):
        """El dummy se enlaza por id (tvg-id) y por nombre a la vez: su
        display-name debe ser el nombre del canal M3U, no la etiqueta
        'Dummy 1h', para que el emparejamiento por nombre también funcione."""
        dummy_id = build_dummy_epg_id("url-canal-raro")
        xml = build_xmltv([self._mapped("Canal Raro", DUMMY_URL, dummy_id)],
                          _epg())
        assert f'<channel id="{dummy_id}">' in xml
        assert "<display-name>Canal Raro</display-name>" in xml
        assert "Dummy 1h" not in xml

    def test_dummy_tambien_enlaza_desde_el_m3u(self):
        """La playlist emite el id sintético del dummy y su display-name."""
        from src.application.services.list_manager.playlist_builder import (
            build_m3u,
        )

        dummy_id = build_dummy_epg_id("url-canal-raro")
        m3u = build_m3u([self._mapped("Canal Raro", DUMMY_URL, dummy_id)])
        extinf = next(l for l in m3u.splitlines() if l.startswith("#EXTINF"))
        assert f'tvg-id="{dummy_id}"' in extinf
        assert extinf.endswith(",Canal Raro")

    def test_dummy_genera_la_parrilla_sintetica(self):
        dummy_id = build_dummy_epg_id("url-canal-raro")
        xml = build_xmltv([self._mapped("Canal Raro", DUMMY_URL, dummy_id)],
                          _epg())
        assert xml.count("<programme") == 168
        assert xml.count("<title>Canal Raro</title>") == 168

    def test_dummy_no_busca_programas_en_la_epg_real(self):
        """Un id dummy no está en la EPG: no debe emitir canal vacío ni fallar."""
        dummy_id = build_dummy_epg_id("url-canal-raro")
        xml = build_xmltv([self._mapped("Canal Raro", DUMMY_URL, dummy_id)],
                          None)
        assert f'<channel id="{dummy_id}">' in xml
        assert xml.count("<programme") == 168

    def test_canal_real_y_dummy_conviven(self):
        dummy_id = build_dummy_epg_id("url-canal-raro")
        channels = [
            self._mapped("La 1 HD", GUIDE_URL, "es.la1"),
            self._mapped("Canal Raro", DUMMY_URL, dummy_id),
        ]
        xml = build_xmltv(channels, _epg())
        assert '<channel id="es.la1">' in xml
        assert f'<channel id="{dummy_id}">' in xml
        # 168 bloques dummy + 1 programa real
        assert xml.count("<programme") == 169

    def test_dos_dummy_no_comparten_canal_ni_parrilla(self):
        a = build_dummy_epg_id("url-a")
        b = build_dummy_epg_id("url-b")
        channels = [
            self._mapped("Uno", "http://s/a", a),
            self._mapped("Dos", "http://s/b", b),
        ]
        xml = build_xmltv(channels, _epg())
        assert xml.count("<programme") == 336
        assert "<title>Uno</title>" in xml and "<title>Dos</title>" in xml
