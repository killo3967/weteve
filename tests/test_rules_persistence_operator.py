"""Persistencia del operador de reglas en ``daemon.toml``."""

from toml_helpers import target, write_daemon

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

    def test_el_fichero_guarda_y_lee_el_operador(self, tmp_path):

        cfg_path = write_daemon(tmp_path / "daemon.toml", targets=[target(
            "0", name="prueba", custom_m3u="x.m3u",
            rules=["group-title:SPAIN:0:and", "custom-filter:HBO:0"])])
        store = TargetStore(cfg_path)
        store.load()
        targets = store.list()
        rules = list(targets.values())[0].rules
        assert rules[0].operator == "and"
        assert rules[1].operator == "or"
