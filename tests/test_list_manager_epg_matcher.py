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
