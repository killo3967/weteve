"""Persistencia del operador: config.ini round-trip con back-compat."""

from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import FilterRule


class TestSerializeRoundtrip:
    def test_and_se_serializa(self):
        r = FilterRule(field="group-title", pattern="SPAIN", operator="and")
        assert r.serialize() == "group-title:SPAIN:0:and"

    def test_or_default_no_se_serializa(self):
        r = FilterRule(field="group-title", pattern="SPAIN")
        assert r.serialize() == "group-title:SPAIN:0"

    def test_roundtrip_and(self):
        raw = "group-title:SPAIN:0:and"
        r = FilterRule.parse(raw)
        assert r.operator == "and" and r.pattern == "SPAIN"

    def test_config_ini_guarda_y_lee_operador(self, tmp_path):
        cfg_path = tmp_path / "config.ini"
        cfg_path.write_text(
            "[DAEMON]\ncontrol_port = 32123\n"
            "[listmanager.target.0]\nname = prueba\ncustom_m3u = x.m3u\n"
            "rules = group-title:SPAIN:0:and|custom-filter:HBO:0\n",
            encoding="utf-8")
        store = TargetStore(cfg_path)
        store.load()
        targets = store.list()
        rules = list(targets.values())[0].rules
        assert rules[0].operator == "and"
        assert rules[1].operator == "or"
