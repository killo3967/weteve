"""Reload del daemon: debe releer config.ini, no su cache de arranque.

Antes, POST /targets/{id}/reload y la herramienta MCP reload_target usaban el
PipelineTarget memorizado cuando el daemon arrancó, de modo que Guardar en la
GUI no tenía efecto en el servicio hasta reiniciar el proceso.
"""
import pytest

from src.application.services.daemon.control_api import ControlApiServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import FilterRule, PipelineTarget


@pytest.fixture()
def store(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[listmanager.target.0]\n"
        "name = viejo\n"
        "custom_m3u = /x.m3u\n"
        "rules = group-title:SPAIN:0\n"
        "port = 8080\n",
        encoding="utf-8")
    s = TargetStore(cfg)
    s.load()
    return s


class _RecordingSupervisor(TargetSupervisor):
    def __init__(self, store):
        super().__init__()
        self.calls = []

    def get_status(self):
        return {"targets": {"0": {"name": "x", "running": True}}}

    def reload_target(self, target_id, target=None, epg_path=None):
        self.calls.append((target_id, target, epg_path))
        return True

    def start_target(self, target_id, target, epg_path=None):
        self.calls.append((target_id, target, epg_path))
        return True

    def stop_target(self, target_id):
        return True


class TestTargetStoreReload:
    def test_reload_recupera_el_target_de_disco(self, store):
        assert store.reload("0").name == "viejo"

    def test_reload_ve_lo_que_escribio_otro_proceso(self, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text("[listmanager.target.0]\nname = viejo\n"
                       "custom_m3u = /x.m3u\nport = 8080\n", encoding="utf-8")
        s = TargetStore(cfg)
        s.load()
        # la GUI reescribe el fichero por su cuenta
        cfg.write_text("[listmanager.target.0]\nname = nuevo\n"
                       "custom_m3u = /x.m3u\n"
                       "rules = custom-filter:!{HBO}:0:and\nport = 8080\n",
                       encoding="utf-8")
        fresh = s.reload("0")
        assert fresh.name == "nuevo"
        assert fresh.rules == (FilterRule("custom-filter", "!{HBO}",
                                          exclude=True, operator="and"),)

    def test_reload_de_target_inexistente_es_none(self, store):
        assert store.reload("ghost") is None


class TestControlApiReloadFromDisk:
    def test_reload_pasa_el_target_fresco(self, store):
        sup = _RecordingSupervisor(store)
        api = ControlApiServer(sup, port=0, target_store=store)
        status, body = api._handle("POST", "/targets/0/reload", auth_ok=False)
        assert (status, body) == (200, {"ok": True})
        tid, target, epg = sup.calls[0]
        assert tid == "0"
        assert target.name == "viejo"

    def test_sin_store_mantiene_comportamiento_previo(self):
        sup = _RecordingSupervisor(None)
        api = ControlApiServer(sup, port=0)
        assert api._handle("POST", "/targets/0/reload", auth_ok=False)[0] == 200
        assert sup.calls[0][1] is None

    def test_target_nuevo_en_disco_se_reconoce(self, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text("[listmanager.target.0]\nname = a\n"
                       "custom_m3u = /x.m3u\nport = 8080\n", encoding="utf-8")
        s = TargetStore(cfg)
        s.load()
        sup = _RecordingSupervisor(s)
        api = ControlApiServer(sup, port=0, target_store=s)
        cfg.write_text("[listmanager.target.0]\nname = a\n"
                       "custom_m3u = /x.m3u\nport = 8080\n"
                       "[listmanager.target.1]\nname = b\n"
                       "custom_m3u = /y.m3u\nport = 8081\n", encoding="utf-8")
        status, _ = api._handle("POST", "/targets/1/reload", auth_ok=False)
        assert status == 200


class TestMcpReloadFromDisk:
    def test_reload_target_fresa_de_disco(self, store):
        sup = _RecordingSupervisor(store)
        reg = build_tool_registry(sup, target_store=store)
        assert reg["reload_target"]["handler"](target_id="0")["ok"] is True
        assert sup.calls[0][1].name == "viejo"

    def test_start_target_fresa_de_disco(self, store):
        sup = _RecordingSupervisor(store)
        reg = build_tool_registry(sup, target_store=store)
        reg["start_target"]["handler"](target_id="0")
        assert sup.calls[0][1].name == "viejo"

    def test_stub_store_sin_reload_no_rompe(self):
        class _LegacyStore:
            def list(self):
                return {"t0": PipelineTarget(name="mem", source_index="/x.m3u")}

            def get(self, tid):
                return self.list().get(tid)

            def get_epg_path(self, tid):
                return None

        sup = _RecordingSupervisor(None)
        reg = build_tool_registry(sup, target_store=_LegacyStore())
        assert reg["reload_target"]["handler"](target_id="t0")["ok"] is True
        assert sup.calls[0][1].name == "mem"
