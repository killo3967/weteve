"""Prioridad de calidad: una sola variante servida por canal.

El proveedor repite el mismo canal con etiquetas distintas (``LA 1 HD``,
``LA 1 FULL HD``, ``LA 1 SD``). Estas pruebas fijan el criterio: gana la de
mayor prioridad, con las de fábrica (4K > FULL HD > HD > sin etiqueta > SD),
empate a favor de la primera y ``0`` = no servir nunca.
"""

from types import SimpleNamespace

from src.application.services.list_manager.quality import (
    DEFAULT_PRIORITY,
    base_name,
    dedupe_variants,
    quality_token,
    weight,
)


def _canal(nombre: str, epg_id: str | None = "La 1 HD", url: str = "") -> object:
    return SimpleNamespace(name=nombre, matched_epg_id=epg_id,
                           url=url or f"http://x/{nombre}")


class TestEtiquetas:
    def test_detecta_la_etiqueta_mas_especifica(self):
        assert quality_token("LA 1 FULL HD") == "full hd"
        assert quality_token("LA 1 HD") == "hd"
        assert quality_token("LA 1 4K") == "4k"
        assert quality_token("LA 1 SD") == "sd"
        assert quality_token("LA 1") == ""

    def test_no_confunde_palabras_que_contienen_hd(self):
        assert quality_token("HDO Sport") == ""       # 'hdo' no es 'hd'
        assert quality_token("La 1 HD+") == "hd"

    def test_prioridad_por_defecto(self):
        assert weight("LA 1 4K") > weight("LA 1 FULL HD")
        assert weight("LA 1 FULL HD") > weight("LA 1 HD")
        assert weight("LA 1 HD") > weight("LA 1")
        assert weight("LA 1") > weight("LA 1 SD")

    def test_tabla_propia_del_target(self):
        """El móvil puede preferir SD: la tabla se pasa por parámetro."""
        movil = {"sd": 5, "": 4, "hd": 3, "full hd": 2, "4k": 0}
        assert weight("LA 1 SD", movil) > weight("LA 1 4K", movil)

    def test_nombre_base_quita_la_etiqueta(self):
        assert base_name("LA 1 FULL HD") == "la 1"
        assert base_name("LA 1 HD") == "la 1"
        assert base_name("DAZN LaLiga SD") == "dazn laliga"
        assert base_name("La 1 Canarias") == "la 1 canarias"


class TestDeduplicacion:
    def test_se_queda_la_mejor_variante(self):
        canales = [_canal("LA 1 HD"), _canal("LA 1 FULL HD"), _canal("LA 1 SD")]
        servidos, duplicados = dedupe_variants(canales)
        assert [c.name for c in servidos] == ["LA 1 FULL HD"]
        assert {c.name for c in duplicados} == {"LA 1 HD", "LA 1 SD"}

    def test_cuatro_k_gana_a_todo(self):
        canales = [_canal("LA 1 SD"), _canal("LA 1"), _canal("LA 1 HD"),
                   _canal("LA 1 FULL HD"), _canal("LA 1 4K")]
        servidos, duplicados = dedupe_variants(canales)
        assert [c.name for c in servidos] == ["LA 1 4K"]
        assert len(duplicados) == 4

    def test_si_solo_hay_full_hd_se_sirve(self):
        servidos, duplicados = dedupe_variants([_canal("LA 1 FULL HD")])
        assert [c.name for c in servidos] == ["LA 1 FULL HD"]
        assert duplicados == []

    def test_si_solo_hay_cuatro_k_se_sirve(self):
        servidos, _ = dedupe_variants([_canal("LA 1 4K")])
        assert [c.name for c in servidos] == ["LA 1 4K"]

    def test_empate_gana_la_primera_del_fichero(self):
        canales = [_canal("LA 1 HD", url="http://a"),
                   _canal("LA 1 HD", url="http://b")]
        servidos, duplicados = dedupe_variants(canales)
        assert [c.url for c in servidos] == ["http://a"]
        assert [c.url for c in duplicados] == ["http://b"]

    def test_cero_es_no_servir_nunca(self):
        movil = {"4k": 0, "hd": 1}
        servidos, duplicados = dedupe_variants(
            [_canal("LA 1 4K")], movil)
        assert servidos == []
        assert [c.name for c in duplicados] == ["LA 1 4K"]

    def test_uno_es_ultimo_recurso(self):
        movil = {"4k": 1, "hd": 5}
        servidos, duplicados = dedupe_variants(
            [_canal("LA 1 4K"), _canal("LA 1 HD")], movil)
        assert [c.name for c in servidos] == ["LA 1 HD"]
        # sin HD disponible, el 4K se sirve igual
        servidos, _ = dedupe_variants([_canal("LA 1 4K")], movil)
        assert [c.name for c in servidos] == ["LA 1 4K"]

    def test_canales_sin_guia_no_se_agrupan(self):
        """Sin id EPG no hay nada que deduplicar (los filtra otra regla)."""
        canales = [_canal("LA 1 HD", epg_id=None), _canal("LA 1 SD", epg_id=None)]
        servidos, duplicados = dedupe_variants(canales)
        assert len(servidos) == 2 and duplicados == []

    def test_mismo_id_epg_pero_nombres_distintos_no_son_variantes(self):
        """Dos canales reales pueden compartir guía: no se borra ninguno."""
        canales = [_canal("La 1", epg_id="La 1"), _canal("La 1 Canarias",
                                                        epg_id="La 1")]
        servidos, duplicados = dedupe_variants(canales)
        assert {c.name for c in servidos} == {"La 1", "La 1 Canarias"}
        assert duplicados == []

    def test_prioridades_de_fabrica_estan_ordenadas(self):
        assert DEFAULT_PRIORITY["4k"] > DEFAULT_PRIORITY["full hd"]
        assert DEFAULT_PRIORITY["sd"] == 1


def test_tabla_vacia_usa_las_prioridades_de_fabrica():
    """Un target sin prioridad configurada no puede dejar todo a cero."""
    canales = [_canal("LA 1 HD"), _canal("LA 1 SD")]
    assert [c.name for c in dedupe_variants(canales, {})[0]] == ["LA 1 HD"]
    assert weight("LA 1 4K", {}) == weight("LA 1 4K", None)
