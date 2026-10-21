"""Tests del matcher EPG del gestor de listas."""

from datetime import datetime

from src.application.services.list_manager.epg_matcher import (
    EpgMatcher,
    brand_key,
    normalize_channel_name,
    parecido,
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


class TestPrioridadDeFuente:
    """El peso de la cartera manda sobre cualquier otro desempate."""

    def _epg(self):
        epg = EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12),
                              datetime(2026, 10, 8, 13), cid)
                      for cid in ("doble.la1hd", "TVE")],
            channel_names={"doble.la1hd": "La 1 HD", "TVE": "LA 1"},
        )
        return epg

    def test_mas_prioridad_gana_aunque_el_otro_nombre_sea_exacto(self):
        epg = self._epg()
        epg._channel_priority = {"doble.la1hd": 0, "TVE": 10}
        assert EpgMatcher(epg).match(None, "LA 1 HD") == "TVE"

    def test_con_la_misma_prioridad_gana_el_nombre_exacto(self):
        epg = self._epg()
        epg._channel_priority = {"doble.la1hd": 5, "TVE": 5}
        assert EpgMatcher(epg).match(None, "LA 1 HD") == "doble.la1hd"

    def test_la_prioridad_gana_a_la_guia_con_metadatos(self):
        from src.domain.entities.epg import ChannelInfo

        epg = self._epg()
        epg._channel_info = {"TVE": ChannelInfo(dial=1)}
        epg._channel_priority = {"doble.la1hd": 10, "TVE": 0}
        assert EpgMatcher(epg).match(None, "LA 1 HD") == "doble.la1hd"


def _matcher_completo() -> EpgMatcher:
    """Guía con las cuatro formas de nombrar el mismo canal."""
    from src.domain.entities.epg import ChannelInfo

    nombres = {
        "MLIGA": "M+ LALIGA",
        "MLIG1": "M+ LALIGA 2",
        "MPLUS": "Movistar Plus",
        "M+ LALIGA TV HD": "M+ LALIGA TV HD",
        "M+ LaLiga HD": "M+ LaLiga",
        "CAZPES": "Caza y Pesca",
        "CHAP4": "M+ Liga de Campeones 5",
        "CPDEP": "M+ Deportes",
    }
    # Solo la guía de Movistar+ publica dial y logo; las otras dos anuncian el
    # mismo canal con otro nombre y sin metadatos.
    con_metadatos = ("MLIGA", "MLIG1", "MPLUS", "CAZPES", "CHAP4", "CPDEP")
    info = {cid: ChannelInfo(dial=i + 1) for i, cid in enumerate(con_metadatos)}
    prioridad = {cid: (10 if cid in info else 0) for cid in nombres}
    return EpgMatcher(EPGData(
        programs=[Program("P", datetime(2026, 10, 8, 12),
                          datetime(2026, 10, 8, 13), cid) for cid in nombres],
        channel_names=nombres, channel_info=info, channel_priority=prioridad,
    ))


class TestMarcaDelNombre:
    def test_la_marca_se_escribe_de_tres_formas(self):
        assert brand_key("M+ LaLiga").palabras == brand_key("Movistar Plus LaLiga").palabras
        assert brand_key("M+ LaLiga").numero == ""

    def test_el_sufijo_tv_no_cambia_el_canal(self):
        assert parecido(brand_key("M+ LaLigaTV"), brand_key("M+ LALIGA")) == 1.0

    def test_el_numero_separa_canales(self):
        assert parecido(brand_key("M+ LaLigaTV 2"), brand_key("M+ LALIGA")) == 0.0

    def test_palabras_pegadas(self):
        assert parecido(brand_key("CAZAyPESCA HD"),
                        brand_key("Caza y Pesca")) > 0.90

    def test_abreviaturas_del_proveedor(self):
        assert parecido(brand_key("M+LCAMPEON5"),
                        brand_key("M+ Liga de Campeones 5")) == 1.0

    def test_anotaciones_entre_parentesis(self):
        assert parecido(brand_key("M+ DEPORTES3 (SOLO EVENTOS)"),
                        brand_key("M+ Deportes 3")) == 1.0


class TestCanonicoSoloConDial:
    """La identidad la da la guía que numera, no la que solo pone logos.

    Las guías XMLTV traen ``channel_info`` con el icono pero sin dial. Si se
    tomaran por canónicas, el mismo canal quedaría partido en dos identidades
    ("CUATRO HD" por Koala, "C4" por Movistar) y se serviría dos veces.
    """

    def test_una_guia_con_logo_pero_sin_dial_no_es_canonica(self):
        from src.domain.entities.epg import ChannelInfo

        nombres = {"C4": "Cuatro", "CUATRO HD": "CUATRO HD"}
        info = {"C4": ChannelInfo(dial=4, logo="http://l/c4.png"),
                "CUATRO HD": ChannelInfo(logo="http://l/koala.png")}
        matcher = EpgMatcher(EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12),
                              datetime(2026, 10, 8, 13), cid) for cid in nombres],
            channel_names=nombres, channel_info=info,
            channel_priority={"C4": 10, "CUATRO HD": 0},
        ))
        assert matcher.canonical_for("CUATRO HD") == "C4"
        assert matcher.canonical_for("CUATRO HD", "CUATRO HD") == "C4"


class TestIdentidadCanonica:
    def test_el_nombre_del_proveedor_llega_al_canal_de_la_guia(self):
        matcher = _matcher_completo()
        assert matcher.canonical_for("M+ LaLigaTVHD") == "MLIGA"
        assert matcher.canonical_for("M+ LaLigaTV 2 HD") == "MLIG1"
        assert matcher.canonical_for("M+LCAMPEON5") == "CHAP4"

    def test_el_id_del_emparejamiento_se_usa_si_el_nombre_lo_confirma(self):
        matcher = _matcher_completo()
        assert matcher.canonical_for("M+ LaLigaTV", "MLIGA") == "MLIGA"

    def test_un_mapeo_manual_no_convierte_dos_canales_en_uno(self):
        """Dos nombres distintos pueden apuntar al mismo id: no son el mismo."""
        matcher = _matcher_completo()
        assert matcher.canonical_for("Canal Sur HD", "MLIGA") is None

    def test_sin_pareja_no_hay_identidad(self):
        matcher = _matcher_completo()
        assert matcher.canonical_for("SYFY FULL HD") is None

    def test_dos_candidatos_igual_de_parecidos_no_deciden_el_dial(self):
        """Sin margen no se elige: un dial equivocado es peor que ninguno."""
        from src.domain.entities.epg import ChannelInfo

        nombres = {"A1": "Canal Nueve", "A2": "Canal Nueve 2",
                   "A3": "Canal Nueve 3"}
        matcher = EpgMatcher(EPGData(
            programs=[Program("P", datetime(2026, 10, 8, 12),
                              datetime(2026, 10, 8, 13), cid) for cid in nombres],
            channel_names=nombres,
            channel_info={cid: ChannelInfo(dial=i) for i, cid in enumerate(nombres)},
        ))
        assert matcher.canonical_for("Canal Nueve HD") == "A1"
        # "Canal Nueve 2" exige número 2: no vale para "Canal Nueve" ni al revés
        assert matcher.canonical_for("Canal Nueve 2") == "A2"
