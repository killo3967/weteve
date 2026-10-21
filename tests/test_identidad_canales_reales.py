"""Identidad canónica con los nombres reales de la lista y de la guía.

Instantánea del 2026-10-20 del target «test»: cada nombre que publica la lista
IPTV con el canal de Movistar+ que le corresponde (o ``null`` si la guía no lo
tiene). Fija el comportamiento del matcher —marca, sufijo numérico,
abreviaturas, anotaciones— y no el catálogo del proveedor: el fixture no se
recalcula solo, así que un cambio en el algoritmo se ve en el diff.
"""

import json
from pathlib import Path

from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.domain.entities.epg import ChannelInfo, EPGData

FIXTURE = Path(__file__).parent / "fixtures" / "identidad_canales.json"


def _datos() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _matcher() -> EpgMatcher:
    guia = _datos()["guia_movistar"]
    return EpgMatcher(EPGData(
        programs=[],
        channel_names={cid: nombre for cid, nombre, _dial in guia},
        channel_info={cid: ChannelInfo(dial=dial) for cid, _n, dial in guia},
        channel_priority={cid: 10 for cid, _n, _d in guia},
    ))


class TestInstantanea:
    def test_los_148_nombres_del_proveedor(self):
        matcher = _matcher()
        fallos = []
        for nombre, esperado in _datos()["canales_iptv"]:
            obtenido = matcher.canonical_for(nombre)
            if obtenido != esperado:
                fallos.append((nombre, esperado, obtenido))
        assert fallos == []

    def test_el_mismo_canal_escrito_de_cuatro_formas(self):
        matcher = _matcher()
        for nombre in ("M+ LaLigaTVHD", "M+ LaLigaTVHD+", "M+ LaLigaTVSD",
                       "M+ LaLigaTV FULL HD"):
            assert matcher.canonical_for(nombre) == "MLIGA"

    def test_cada_numero_va_a_su_canal(self):
        matcher = _matcher()
        assert matcher.canonical_for("M+ LaLigaTV 2 HD") == "MLIG1"
        assert matcher.canonical_for("M+ LaLigaTV 3 HD") == "MLIG2"
        assert matcher.canonical_for("M+ LaLigaTV 4") == "MLIG3"

    def test_el_nombre_viejo_del_proveedor_llega_igual(self):
        matcher = _matcher()
        assert matcher.canonical_for("M+LCAMPEON5") == "CHAP4"
        assert matcher.canonical_for("CAZAyPESCA HD") == "CAZPES"
        assert matcher.canonical_for("Movistar Plus+ HD") == "MPLUS"

    def test_lo_que_no_esta_en_la_guia_no_se_inventa(self):
        matcher = _matcher()
        for nombre in ("CANAL SUR HD", "SYFY FULL HD", "FDF FULL HD", "BOM"):
            assert matcher.canonical_for(nombre) is None
