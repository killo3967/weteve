"""Operador AND/OR entre reglas y campos de grupo dinámicos."""


from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import FilterRule


# campo group-title con múltiples grupos vía Channel.groups
def _ch_eval(groups, rules):
    return evaluate_channel("Canal X", groups.get("group-title", ["Otros"])[0],
                            "", rules, channel_groups=groups)

class TestDynamicGroupFields:
    def test_campo_tvg_group_coincide(self):
        rule = FilterRule(field="tvg-group", pattern="Rock")
        assert _ch_eval({"tvg-group": ["Music/Rock"]}, [rule]) is True

    def test_campo_extgrp(self):
        rule = FilterRule(field="extgrp", pattern="Entretenimiento")
        assert _ch_eval({"extgrp": ["Entretenimiento"]}, [rule]) is True

    def test_campo_no_presente_no_coincide(self):
        rule = FilterRule(field="tvg-group", pattern="Rock")
        assert _ch_eval({"group-title": ["SPAIN"]}, [rule]) is False

    def test_multiple_grupos_matchea_cualquiera(self):
        rule = FilterRule(field="group-title", pattern="HD")
        assert _ch_eval({"group-title": ["Sports", "HD Channels"]}, [rule]) is True

    def test_group_title_sigue_funcionando(self):
        rule = FilterRule(field="group-title", pattern="SPAIN")
        assert _ch_eval({"group-title": ["SPAIN"]}, [rule]) is True

class TestOperators:
    def test_default_or_dos_reglas_any(self):
        # SPAIN o USA: pasa si cualquiera coincide
        rules = [
            FilterRule(field="group-title", pattern="SPAIN"),
            FilterRule(field="group-title", pattern="USA"),
        ]
        assert _ch_eval({"group-title": ["USA"]}, rules) is True

    def test_and_exige_todas(self):
        rules = [
            FilterRule(field="group-title", pattern="SPAIN", operator="and"),
            FilterRule(field="group-title", pattern="HD", operator="and"),
        ]
        assert _ch_eval({"group-title": ["SPAIN", "HD Channels"]}, rules) is True
        assert _ch_eval({"group-title": ["SPAIN"]}, rules) is False

    def test_mezcla_and_or(self):
        # (SPAIN) Y (HD o Deportes)
        rules = [
            FilterRule(field="group-title", pattern="SPAIN", operator="and"),
            FilterRule(field="group-title", pattern="HD", operator="or"),
            FilterRule(field="group-title", pattern="Deportes", operator="or"),
        ]
        assert _ch_eval({"group-title": ["SPAIN", "HD"]}, rules) is True
        assert _ch_eval({"group-title": ["SPAIN", "Cine"]}, rules) is False
        assert _ch_eval({"group-title": ["USA", "HD"]}, rules) is False

    def test_sin_reglas_pasa(self):
        assert _ch_eval({}, []) is True

class TestSerializeOperator:
    def test_serialize_con_and(self):
        r = FilterRule(field="group-title", pattern="X", operator="and")
        assert r.serialize() == "group-title:X:0:and"

    def test_default_no_cambia_formato(self):
        r = FilterRule(field="group-title", pattern="X")
        assert r.serialize() == "group-title:X:0"

    def test_parse_lee_operador(self):
        r = FilterRule.parse("group-title:X:0:and")
        assert r.operator == "and"

    def test_parse_legacy_sin_operador(self):
        r = FilterRule.parse("group-title:X:0")
        assert r.operator == "or"
