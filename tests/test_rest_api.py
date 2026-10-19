"""API REST v1: adaptador fino sobre TargetService (sin lógica propia)."""

import pytest

from src.infrastructure.web.rest_api import RestApi


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.targets = {"0": {"name": "test", "m3u": "C:/x.m3u", "port": 9999}}

    def status(self) -> dict:
        return {"targets": {"0": {"name": "test", "running": True, "port": 9999}}}

    def list_targets(self) -> dict:
        return {"targets": dict(self.targets)}

    def get_target(self, target_id: str):
        t = self.targets.get(target_id)
        return {"target": dict(t)} if t else None

    def runtime_status(self, target_id: str):
        return {"name": "test", "running": True}

    def channels(self, target_id, limit=50, offset=0, unmatched_only=False):
        self.calls.append(("channels", limit, offset, unmatched_only))
        return {"channels": [{"name": "Canal A"}], "total": 1, "count": 1,
                "offset": offset}

    def mapping_candidates(self, target_id, limit=200, offset=0,
                           include_served=False):
        self.calls.append(("mapping", limit, offset, include_served))
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def groups(self, target_id: str):
        self.calls.append(("groups", target_id))
        return {"groups": ["Cine"]}

    def group_fields(self, target_id: str):
        return {"fields": {"group-title": ["Cine"]}}

    def preview(self, target_id: str, rules):
        self.calls.append(("preview", target_id, rules))
        return {"channels": ["Canal A"], "count": 1, "total": 2}

    def epg_channels(self, target_id, limit=200, offset=0):
        self.calls.append(("epg", limit, offset))
        return {"channels": [], "total": 0, "count": 0, "offset": offset}


@pytest.fixture
def api() -> RestApi:
    return RestApi(_FakeService())


class TestLecturas:
    def test_health(self, api):
        assert api.handle("GET", "/api/v1/health") == (
            200, {"ok": True, "api": "v1"})

    def test_status(self, api):
        code, body = api.handle("GET", "/api/v1/status")
        assert code == 200
        assert body["targets"]["0"]["running"] is True

    def test_targets(self, api):
        code, body = api.handle("GET", "/api/v1/targets")
        assert code == 200 and "0" in body["targets"]

    def test_target(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0")
        assert code == 200 and body["target"]["name"] == "test"

    def test_target_inexistente(self, api):
        code, body = api.handle("GET", "/api/v1/targets/ghost")
        assert code == 404 and "no existe" in body["error"]

    def test_channels_por_defecto(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0/channels")
        assert code == 200 and body["total"] == 1
        assert api._service.calls[-1] == ("channels", 50, 0, False)

    def test_channels_con_parametros(self, api):
        api.handle(
            "GET",
            "/api/v1/targets/0/channels?limit=10&offset=5&unmatched_only=1")
        assert api._service.calls[-1] == ("channels", 10, 5, True)

    def test_mapping_candidates(self, api):
        code, _ = api.handle("GET", "/api/v1/targets/0/mapping-candidates")
        assert code == 200
        assert api._service.calls[-1] == ("mapping", 200, 0, False)

    def test_mapping_candidates_incluye_servidos(self, api):
        """La tabla de mapeo puede pedir también los canales que ya sirven."""
        code, _ = api.handle(
            "GET", "/api/v1/targets/0/mapping-candidates?include_served=true")
        assert code == 200
        assert api._service.calls[-1] == ("mapping", 200, 0, True)

    def test_epg_channels(self, api):
        code, _ = api.handle("GET", "/api/v1/targets/0/epg-channels?limit=5")
        assert code == 200
        assert api._service.calls[-1] == ("epg", 5, 0)

    def test_groups(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0/groups")
        assert code == 200 and body["groups"] == ["Cine"]

    def test_group_fields(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0/group-fields")
        assert code == 200 and body["fields"]["group-title"] == ["Cine"]

    def test_preview(self, api):
        code, body = api.handle("POST", "/api/v1/targets/0/preview",
                                b'{"rules": ["group-title:Cine:0:or"]}')
        assert code == 200 and body["total"] == 2
        assert api._service.calls[-1] == ("preview", "0",
                                          ["group-title:Cine:0:or"])

    def test_preview_reglas_invalidas(self, api):
        code, body = api.handle("POST", "/api/v1/targets/0/preview",
                                b'{"rules": "x"}')
        assert code == 400 and "lista" in body["error"]

    def test_status_de_target(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0/status")
        assert code == 200 and body["running"] is True


class TestErrores:
    def test_ruta_desconocida(self, api):
        assert api.handle("GET", "/api/v1/nada")[0] == 404

    def test_fuera_del_prefijo(self, api):
        assert api.handle("GET", "/otra")[0] == 404

    def test_metodo_no_soportado(self, api):
        code, body = api.handle("DELETE", "/api/v1/targets/0/rules")
        assert code == 405 and "no soportado" in body["error"]

    def test_limit_no_numerico(self, api):
        code, body = api.handle("GET", "/api/v1/targets/0/channels?limit=x")
        assert code == 400 and "entero" in body["error"]

    def test_offset_negativo(self, api):
        code, _ = api.handle("GET", "/api/v1/targets/0/channels?offset=-3")
        assert code == 400

    def test_limit_se_capa(self, api):
        api.handle("GET", "/api/v1/targets/0/channels?limit=99999")
        assert api._service.calls[-1][1] == 1000


class _WritableService(_FakeService):
    """FakeService + escrituras: solo registra lo que le llega."""

    def update_settings(self, target_id, **kw):
        self.calls.append(("settings", target_id, kw))
        if kw.get("port") == 1:
            return {"error": "port fuera de rango (1-65535)"}
        return {"ok": True, "target": {"name": "test"}}

    def set_rules(self, target_id, rules, reload=True):
        self.calls.append(("rules", target_id, rules, reload))
        return {"ok": True}

    def set_mappings(self, target_id, mappings, reload=True):
        self.calls.append(("mappings", target_id, mappings, reload))
        return {"ok": True, "applied": mappings, "errors": []}

    def set_disabled(self, target_id, urls, reload=True):
        self.calls.append(("disabled", target_id, urls, reload))
        return {"ok": True}

    def run(self, target_id):
        self.calls.append(("run", target_id))
        return {"filtered_count": 3}

    def start_service(self, target_id):
        self.calls.append(("start", target_id))
        return {"ok": True, "status": {"running": True}}

    def stop_service(self, target_id):
        self.calls.append(("stop", target_id))
        return {"ok": True, "status": {"running": False}}


def _body(payload: dict) -> bytes:
    import json

    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def wapi() -> RestApi:
    return RestApi(_WritableService())


class TestEscrituras:
    def test_settings(self, wapi):
        code, body = wapi.handle("PUT", "/api/v1/targets/0/settings",
                                 _body({"name": "n", "port": 9998}))
        assert code == 200 and body["ok"] is True
        assert wapi._service.calls[-1] == (
            "settings", "0", {"name": "n", "port": 9998, "bind": None,
                              "quality_priority": None, "reload": True})

    def test_settings_sin_reload(self, wapi):
        wapi.handle("PUT", "/api/v1/targets/0/settings",
                    _body({"port": 9998, "reload": False}))
        assert wapi._service.calls[-1][2]["reload"] is False

    def test_settings_rechazado_es_400(self, wapi):
        code, body = wapi.handle("PUT", "/api/v1/targets/0/settings",
                                 _body({"port": 1}))
        assert code == 400 and "fuera de rango" in body["error"]

    def test_rules(self, wapi):
        rules = ["group-title:SPAIN:0:and", "custom-filter:!{HBO}:0:and"]
        code, _ = wapi.handle("PUT", "/api/v1/targets/0/rules",
                              _body({"rules": rules}))
        assert code == 200
        assert wapi._service.calls[-1] == ("rules", "0", rules, True)

    def test_rules_tipo_invalido(self, wapi):
        code, body = wapi.handle("PUT", "/api/v1/targets/0/rules",
                                 _body({"rules": "no-es-lista"}))
        assert code == 400 and "lista" in body["error"]

    def test_mappings(self, wapi):
        mappings = [{"channel_url": "http://s/a", "epg_id": "a.tv"},
                    {"channel_url": "http://s/b", "epg_id": None}]
        code, body = wapi.handle("PUT", "/api/v1/targets/0/mappings",
                                 _body({"mappings": mappings}))
        assert code == 200 and body["errors"] == []
        assert wapi._service.calls[-1][2] == mappings

    def test_mappings_tipo_invalido(self, wapi):
        code, _ = wapi.handle("PUT", "/api/v1/targets/0/mappings",
                              _body({"mappings": {}}))
        assert code == 400

    def test_disabled(self, wapi):
        code, _ = wapi.handle("PUT", "/api/v1/targets/0/disabled",
                              _body({"disabled_channels": ["http://s/b"]}))
        assert code == 200
        assert wapi._service.calls[-1] == (
            "disabled", "0", ["http://s/b"], True)

    def test_run(self, wapi):
        code, body = wapi.handle("POST", "/api/v1/targets/0/run")
        assert code == 200 and body["filtered_count"] == 3

    def test_service_start_y_stop(self, wapi):
        assert wapi.handle("POST", "/api/v1/targets/0/service",
                           _body({"action": "start"}))[1]["status"]["running"]
        assert not wapi.handle("POST", "/api/v1/targets/0/service",
                               _body({"action": "stop"}))[1]["status"]["running"]

    def test_service_accion_invalida(self, wapi):
        code, _ = wapi.handle("POST", "/api/v1/targets/0/service",
                              _body({"action": "restart"}))
        assert code == 400

    def test_cuerpo_no_json(self, wapi):
        code, body = wapi.handle("PUT", "/api/v1/targets/0/rules",
                                 b"{no json")
        assert code == 400 and "no-JSON" in body["error"]

    def test_accion_desconocida(self, wapi):
        assert wapi.handle("PUT", "/api/v1/targets/0/otra",
                           _body({}))[0] == 404

    def test_target_inexistente(self, wapi):
        wapi._service.targets.clear()
        assert wapi.handle("PUT", "/api/v1/targets/0/rules",
                           _body({"rules": []}))[0] == 404


class TestCarteraPorRest:
    def test_get_sources(self, wapi):
        wapi._service.sources = lambda: {"sources": [{"id": "a"}]}
        code, body = wapi.handle("GET", "/api/v1/sources")
        assert code == 200 and body["sources"][0]["id"] == "a"

    def test_put_sources(self, wapi):
        enviado = {}
        wapi._service.set_sources = lambda sources: enviado.update(
            sources=sources) or {"ok": True, "sources": sources, "errors": []}
        code, body = wapi.handle("PUT", "/api/v1/sources",
                                 b'{"sources": [{"name": "x", "kind": "m3u",'
                                 b' "location": "C:/x.m3u"}]}')
        assert code == 200 and body["ok"] is True
        assert enviado["sources"][0]["location"] == "C:/x.m3u"

    def test_put_sources_tipo_invalido(self, wapi):
        code, _ = wapi.handle("PUT", "/api/v1/sources", b'{"sources": "x"}')
        assert code == 400

    def test_put_target_sources(self, wapi):
        enviado = {}
        wapi._service.set_target_sources = (
            lambda tid, m3u, epg, reload=True: enviado.update(
                tid=tid, m3u=m3u, epg=epg, reload=reload) or {"ok": True})
        code, _ = wapi.handle(
            "PUT", "/api/v1/targets/0/sources",
            b'{"m3u_sources": ["a"], "epg_sources": ["b"], "reload": false}')
        assert code == 200
        assert enviado == {"tid": "0", "m3u": ["a"], "epg": ["b"],
                           "reload": False}

    def test_put_target_sources_tipo_invalido(self, wapi):
        code, _ = wapi.handle("PUT", "/api/v1/targets/0/sources",
                              b'{"m3u_sources": "x", "epg_sources": []}')
        assert code == 400


class TestApagadoPorLaApi:
    """El demonio se apaga por la misma API con la que se habla."""

    def test_post_shutdown_llama_al_apagado(self, wapi):
        apagado = []
        wapi._on_shutdown = lambda: apagado.append(True)
        code, body = wapi.handle("POST", "/api/v1/shutdown", b"{}")
        assert code == 200 and body == {"ok": True}
        assert apagado == [True]

    def test_shutdown_solo_admite_post(self, wapi):
        code, _ = wapi.handle("GET", "/api/v1/shutdown")
        assert code == 405
