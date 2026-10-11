"""Tests del motor de filtros del gestor de listas."""

import pytest

from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import FilterRule


class TestNoRules:
    def test_sin_reglas_todo_pasa(self):
        assert evaluate_channel("La 1", "General", "", []) is True

    def test_reglas_vacias_o_sin_patron_todo_pasa(self):
        rules = [FilterRule(field="custom-filter", pattern="")]
        assert evaluate_channel("La 1", "General", "", rules) is True


class TestGroupTitle:
    def test_grupo_coincide_exacto(self):
        rule = FilterRule(field="group-title", pattern="Sports")
        assert evaluate_channel("Canal A", "Sports", "", [rule]) is True

    def test_grupo_no_coincide(self):
        rule = FilterRule(field="group-title", pattern="Sports")
        assert evaluate_channel("Canal A", "News", "", [rule]) is False

    def test_group_title_insensible_a_mayusculas(self):
        rule = FilterRule(field="group-title", pattern="sports")
        assert evaluate_channel("Canal A", "SPORTS", "", [rule]) is True

    def test_group_title_case_sensitive(self):
        rule = FilterRule(field="group-title", pattern="sports", case_sensitive=True)
        assert evaluate_channel("Canal A", "SPORTS", "", [rule]) is False


class TestCustomFilter:
    def test_subcadena_en_nombre(self):
        rule = FilterRule(field="custom-filter", pattern="deportes")
        assert evaluate_channel("Canal de Deportes HD", "General", "", [rule]) is True

    def test_subcadena_en_atributos_crudos(self):
        attrs = 'tvg-id="es.la1" tvg-name="La 1" group-title="General"'
        rule = FilterRule(field="custom-filter", pattern="es.la1")
        assert evaluate_channel("La 1", "General", attrs, [rule]) is True

    def test_sin_coincidencia(self):
        rule = FilterRule(field="custom-filter", pattern="xxx")
        assert evaluate_channel("La 1", "General", "", [rule]) is False


class TestIncludeExcludeBraces:
    def test_exclude_elimina_canal(self):
        # patrón base vacío con !{...}: pasa todo salvo lo excluido
        rule = FilterRule(field="custom-filter", pattern="!{XXX}")
        assert evaluate_channel("Canal XXX", "General", "", [rule]) is False
        assert evaluate_channel("Canal normal", "General", "", [rule]) is True

    def test_include_adicional(self):
        # grupo Sports + incluir {deportes}
        rule = FilterRule(field="group-title", pattern="Sports {deportes}")
        assert evaluate_channel("Canal D", "Deportes", "", [rule]) is True
        assert evaluate_channel("Canal S", "Sports", "", [rule]) is True
        assert evaluate_channel("Canal X", "News", "", [rule]) is False

    def test_exclude_tiene_prioridad_sobre_include(self):
        rule = FilterRule(field="custom-filter", pattern=" {deportes} !{XXX}")
        assert evaluate_channel("Canal XXX Deportes", "General", "", [rule]) is False
        assert evaluate_channel("Canal Deportes", "General", "", [rule]) is True


class TestMultipleRules:
    def test_cualquier_regla_que_incluya_gana(self):
        rules = [
            FilterRule(field="group-title", pattern="Sports"),
            FilterRule(field="custom-filter", pattern="noticias"),
        ]
        assert evaluate_channel("Canal de Noticias", "News", "", rules) is True
        assert evaluate_channel("Canal Sports", "Sports", "", rules) is True
        assert evaluate_channel("Canal Otro", "Cine", "", rules) is False

    def test_exclude_en_cualquier_regla_elimina(self):
        rules = [
            FilterRule(field="group-title", pattern="Sports"),
            FilterRule(field="custom-filter", pattern="!{xxx}"),
        ]
        assert evaluate_channel("Canal XXX", "Sports", "", rules) is False
        assert evaluate_channel("Canal Deporte", "Sports", "", rules) is True


@pytest.mark.parametrize(
    ("pattern", "name", "group", "expected"),
    [
        ("SPAIN", "La 1 SPAIN", "General", True),
        ("SPAIN", "La 1", "spain", True),
        ("SPAIN", "La 1", "General", False),
    ],
)
def test_casos_proyecto(pattern, name, group, expected):
    """Casos derivados del campo ``filter`` del config.ini existente."""
    rule = FilterRule(field="custom-filter", pattern=pattern)
    assert evaluate_channel(name, group, "", [rule]) is expected
