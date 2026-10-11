"""Normalización de espacios en el parseo M3U.

Listas reales traen "MOVISTAR  CINE" (doble espacio), espacios sobrantes o
grupos que son solo espacios. El filtro por subcadena es byte a byte, así que
la normalización se hace en el parseo para que lo persistido ya esté limpio.
"""

from src.infrastructure.adapters.file_m3u_repository import (
    FileM3URepository,
    normalize_spacing,
)

def _channels(m3u_text: str):
    repo = FileM3URepository()
    lines = m3u_text.splitlines()
    return repo._parse_m3u(lines)

class TestNormalizeSpacing:
    def test_colapsa_dobles_espacios(self):
        assert normalize_spacing("MOVISTAR  CINE") == "MOVISTAR CINE"

    def test_recorta_extremos(self):
        assert normalize_spacing("  CINE  ") == "CINE"

    def test_tabs_y_saltos(self):
        assert normalize_spacing("CINE\t\n ESTRENO") == "CINE ESTRENO"

    def test_deja_texto_limpio_igual(self):
        assert normalize_spacing("La 1 HD") == "La 1 HD"

    def test_vacio_y_espacios_solos(self):
        assert normalize_spacing("") == ""
        assert normalize_spacing("   ") == ""

class TestParseM3UNormalizado:
    def test_nombre_y_grupo_sin_dobles_espacios(self):
        chs = _channels(
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="c1" tvg-name="MOVISTAR  CINE" '
            'group-title="Deportes  Boxeo  ",MOVISTAR  CINE  HD\n'
            "http://x/1\n"
        )
        assert len(chs) == 1
        assert chs[0].name == "MOVISTAR CINE"  # tvg-name tiene prioridad
        assert chs[0].group == "Deportes Boxeo"

    def test_grupo_solo_espacios_cae_a_otros(self):
        chs = _channels(
            "#EXTM3U\n"
            '#EXTINF:-1 group-title="   ",Canal X\n'
            "http://x/2\n"
        )
        assert chs[0].group == "Otros"
