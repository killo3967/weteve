"""Tests del matcher EPG del gestor de listas."""

from datetime import datetime

from src.application.services.list_manager.epg_matcher import (
    EpgMatcher,
    normalize_channel_name,
)
from src.domain.entities.epg import EPGData, Program


def _epg() -> EPGData:
    programs = [
        Program("Noticias 1", datetime(2026, 10, 8, 12, 0), datetime(2026, 10, 8, 13, 0), "es.la1"),
        Program("Deportes", datetime(2026, 10, 8, 13, 0), datetime(2026, 10, 8, 14, 0), "es.la1"),
        Program("Informativos", datetime(2026, 10, 8, 12, 0), datetime(2026, 10, 8, 13, 0), "es.24h"),
    ]
    return EPGData(
        programs=programs,
        channel_names={
            "es.la1": "La 1",
            "es.24h": "Canal 24 Horas",
        },
    )


class TestNormalizeChannelName:
    def test_sin_acentos_ni_mayusculas(self):
        assert normalize_channel_name("Televisión Española  HD") == "television espanola hd"

    def test_vacio(self):
        assert normalize_channel_name("") == ""

    def test_collapse_espacios(self):
        assert normalize_channel_name("La   1") == "la 1"


class TestMatchById:
    def test_tvg_id_exacto(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match("es.la1", "La Uno") == "es.la1"

    def test_tvg_id_case_insensitive(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match("ES.LA1", "x") == "es.la1"

    def test_tvg_id_vacio_cae_a_nombre(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "La 1") == "es.la1"


class TestMatchByName:
    def test_nombre_normalizado(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "la  1") == "es.la1"

    def test_nombre_con_acentos(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "Canal 24 Horás") == "es.24h"

    def test_sin_match(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "Canal Desconocido") is None


class TestSuggestions:
    def test_sugerencias_ordenadas(self):
        matcher = EpgMatcher(_epg())
        s = matcher.suggestions("La Uno")
        assert s, "debe haber al menos una sugerencia"
        assert s[0][0] == "es.la1"

    def test_limite(self):
        matcher = EpgMatcher(_epg())
        s = matcher.suggestions("Canal", limit=1)
        assert len(s) <= 1

    def test_nombre_vacio_sin_sugerencias(self):
        matcher = EpgMatcher(_epg())
        assert matcher.suggestions("") == []


class TestOverridesEnPipeline:
    def test_channel_names_expuesto(self):
        """El pipeline necesita channel_names para el editor manual."""
        epg = _epg()
        matcher = EpgMatcher(epg)
        assert epg.channel_names["es.la1"] == "La 1"
        assert "es.la1" in matcher.match("es.la1", "x")


class TestMatchPorNombreBase:
    """El proveedor etiqueta la calidad en el nombre y la guía no la lleva."""

    def test_canal_con_hd_casa_con_la_guia_sin_hd(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "La 1 HD") == "es.la1"
        assert matcher.match(None, "LA 1 FULL HD") == "es.la1"

    def test_el_nombre_exacto_gana_al_nombre_base(self):
        epg = EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12), datetime(2026, 10, 8, 13), cid)
                      for cid in ("es.la1", "es.la1hd")],
            channel_names={"es.la1": "La 1", "es.la1hd": "La 1 HD"},
        )
        matcher = EpgMatcher(epg)
        assert matcher.match(None, "La 1 HD") == "es.la1hd"
        assert matcher.match(None, "La 1") == "es.la1"

    def test_sin_coincidencia_sigue_devolviendo_none(self):
        matcher = EpgMatcher(_epg())
        assert matcher.match(None, "Canal Inventado HD") is None


class TestPrioridadDeLaGuiaConMetadatos:
    """Dos guías anuncian el mismo canal: gana la que trae dial y logo."""

    def test_gana_la_guia_que_publica_dial_y_logo(self):
        from src.domain.entities.epg import ChannelInfo

        epg = EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12),
                              datetime(2026, 10, 8, 13), cid)
                      for cid in ("doble.la1hd", "TVE")],
            # La guía general lo anuncia con la etiqueta de calidad; la de
            # Movistar+ sin ella, pero con dial y logo.
            channel_names={"doble.la1hd": "La 1 HD", "TVE": "LA 1"},
            channel_info={"TVE": ChannelInfo(dial=1, logo="http://l/tve.png")},
        )
        matcher = EpgMatcher(epg)
        assert matcher.match(None, "LA 1 HD") == "TVE"

    def test_sin_metadatos_manda_el_nombre_exacto(self):
        epg = EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12),
                              datetime(2026, 10, 8, 13), cid)
                      for cid in ("doble.la1hd", "otra.la1")],
            channel_names={"doble.la1hd": "La 1 HD", "otra.la1": "LA 1"},
        )
        matcher = EpgMatcher(epg)
        assert matcher.match(None, "LA 1 HD") == "doble.la1hd"
