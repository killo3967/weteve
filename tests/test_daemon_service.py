"""TargetService: fachada de dominio común a REST y MCP."""

from src.application.services.daemon.service import (
    TargetService,
    parse_rules,
    serialize_target,
)
from src.application.services.list_manager.models import PipelineTarget


class _StubSupervisor:
    def __init__(self) -> None:
        self.reloads: list[tuple] = []
        self.reload_ok = True
        self.snapshot_ready = True
        self.calls: list[tuple] = []

    def get_status(self) -> dict:
        return {"targets": {"0": {"name": "t", "running": True}}}

    def list_tv_channels(self, target_id, limit=50, offset=0,
                         unmatched_only=False) -> dict:
        self.calls.append(("channels", target_id, limit, offset, unmatched_only))
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def mapping_candidates(self, target_id, limit=200, offset=0,
                           include_served=False) -> dict:
        self.calls.append(("mapping", target_id, limit, offset,
                           include_served))
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def list_epg_channels(self, target_id, limit=200, offset=0) -> dict:
        self.calls.append(("epg", target_id, limit, offset))
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def suggest_epg_matches(self, target_id, channel_name, limit=5) -> dict:
        return {"suggestions": [], "channel_name": channel_name}

    def groups(self, target_id: str) -> list[str]:
        return ["Cine", "Deportes"]

    def group_fields(self, target_id: str) -> dict:
        return {"group-title": ["Cine"]}

    def preview_filter(self, target_id: str, rules) -> dict:
        self.calls.append(("preview", target_id, [r.pattern for r in rules]))
        return {"channels": ["Canal A"], "count": 1, "total": 2}

    def has_snapshot(self, target_id: str) -> bool:
        return self.snapshot_ready

    def reload_target(self, target_id, target=None, epg_path=None) -> bool:
        self.reloads.append((target_id, target, epg_path))
        if self.reload_ok:
            self.snapshot_ready = True  # como el supervisor real: deja resultado
        return self.reload_ok

    def start_target(self, target_id, target, epg_path=None,
                     bind=None) -> bool:
        self.calls.append(("start", target_id))
        return True

    def stop_target(self, target_id) -> bool:
        self.calls.append(("stop", target_id))
        return True

    def metrics(self, target_id: str) -> dict:
        return {"total_input": 4, "filtered_count": 2, "unmatched_count": 0}


class _StubStore:
    def __init__(self) -> None:
        self.targets = {"0": PipelineTarget(
            name="t", source_index="C:/x.m3u", port=9999,
            rules=parse_rules(["group-title:SPAIN:0:and"]))}
        self.epg_paths = {"0": "C:/tv.xml"}
        self.reloads = 0
        self.saves: list[tuple] = []

    def save(self, target_id, target, epg_path=None, bind=None) -> None:
        self.saves.append((target_id, target, epg_path, bind))
        self.targets[target_id] = target
        self.epg_paths[target_id] = epg_path

    def get_bind(self, target_id):
        return "127.0.0.1"

    def list(self) -> dict:
        return dict(self.targets)

    def get(self, target_id):
        return self.targets.get(target_id)

    def reload(self, target_id):
        self.reloads += 1
        return self.targets.get(target_id)

    def get_epg_path(self, target_id):
        return self.epg_paths.get(target_id)


def _service():
    return TargetService(_StubSupervisor(), _StubStore())


class TestLecturas:
    def test_list_targets_serializa_reglas(self):
        body = _service().list_targets()
        assert body["targets"]["0"]["rules"] == ["group-title:SPAIN:0:and"]

    def test_get_target_inexistente(self):
        assert _service().get_target("ghost") is None

    def test_get_target_usa_la_definicion_de_disco(self):
        service = _service()
        assert service.get_target("0")["target"]["port"] == 9999
        assert service._store.reloads == 1

    def test_groups_delega(self):
        assert _service().groups("0") == {"groups": ["Cine", "Deportes"]}

    def test_group_fields_delega(self):
        assert _service().group_fields("0") == {"fields": {"group-title": ["Cine"]}}

    def test_preview_parsea_reglas_y_delega(self):
        service = _service()
        out = service.preview("0", ["group-title:Cine:0:or", "custom-filter:!{HBO}:0:and"])
        assert out["total"] == 2 and out["channels"] == ["Canal A"]
        assert service._supervisor.calls[-1] == ("preview", "0", ["Cine", "!{HBO}"])

    def test_preview_reglas_invalidas(self):
        assert "error" in _service().preview("0", "no-es-lista")

    def test_runtime_status(self):
        assert _service().runtime_status("0")["running"] is True

    def test_channels_delega_parametros(self):
        service = _service()
        service.channels("0", limit=5, offset=2, unmatched_only=True)
        assert service._supervisor.calls[-1] == ("channels", "0", 5, 2, True)

    def test_channels_rellena_el_snapshot_si_falta(self):
        service = _service()
        service._supervisor.snapshot_ready = False
        service.channels("0")
        assert len(service._supervisor.reloads) == 1
        service.channels("0")
        assert len(service._supervisor.reloads) == 1

    def test_mapping_candidates_delega(self):
        service = _service()
        service.mapping_candidates("0", limit=7, offset=1)
        assert service._supervisor.calls[-1] == ("mapping", "0", 7, 1, False)

    def test_mapping_candidates_con_servidos(self):
        service = _service()
        service.mapping_candidates("0", include_served=True)
        assert service._supervisor.calls[-1][4] is True

    def test_epg_channels_delega(self):
        service = _service()
        service.epg_channels("0", limit=3, offset=0)
        assert service._supervisor.calls[-1] == ("epg", "0", 3, 0)


class TestRun:
    def test_una_ejecucion_y_metricas(self):
        service = _service()
        metrics = service.run("0")
        assert metrics["filtered_count"] == 2
        assert len(service._supervisor.reloads) == 1
        target_id, target, epg_path = service._supervisor.reloads[0]
        assert target_id == "0" and epg_path == "C:/tv.xml"
        assert target.name == "t"

    def test_target_inexistente(self):
        assert "error" in _service().run("ghost")

    def test_fallo_de_ejecucion(self):
        service = _service()
        service._supervisor.reload_ok = False
        assert "no ejecutó" in service.run("0")["error"]


class TestSerializacion:
    def test_serialize_target_incluye_refs(self):
        from src.application.services.list_manager.models import SourceRef
        t = PipelineTarget(
            name="t", source_index="merged",
            source_refs=(SourceRef("a", "lista", "C:/a.m3u"),))
        data = serialize_target(t)
        assert data["source_refs"] == [
            {"id": "a", "name": "lista", "location": "C:/a.m3u"}]

    def test_parse_rules_ignora_vacias(self):
        assert len(parse_rules(["", "  ", "group-title:X:0:and"])) == 1


class TestEscritura:
    def test_set_rules_guarda_y_recarga(self):
        service = _service()
        out = service.set_rules("0", ["group-title:Cine:0:or",
                                      "custom-filter:!{HBO}:0:and"])
        assert out["ok"] is True and out["reloaded"] is True
        assert len(service._store.saves) == 1
        assert len(service._supervisor.reloads) == 1
        rules = service._store.saves[0][1].rules
        assert [r.pattern for r in rules] == ["Cine", "!{HBO}"]
        assert rules[1].exclude is True and rules[1].operator == "and"

    def test_set_rules_sin_reload(self):
        service = _service()
        out = service.set_rules("0", ["group-title:Cine:0:or"], reload=False)
        assert out["reloaded"] is False
        assert service._supervisor.reloads == []

    def test_set_mappings_mezcla_y_borra(self):
        service = _service()
        service._store.targets["0"] = PipelineTarget(
            name="t", source_index="C:/x.m3u", epg_overrides={"u1": "a"})
        out = service.set_mappings("0", [
            {"channel_url": "u1", "epg_id": None},
            {"channel_url": "u2", "epg_id": "b"},
        ])
        assert out["errors"] == []
        assert out["applied"] == [{"channel_url": "u1", "epg_id": ""},
                                  {"channel_url": "u2", "epg_id": "b"}]
        assert service._store.saves[0][1].epg_overrides == {"u2": "b"}

    def test_set_mappings_borrar_inexistente_es_parcial(self):
        service = _service()
        out = service.set_mappings("0", [{"channel_url": "nada", "epg_id": None}])
        assert out["partial"] is True and out["applied"] == []

    def test_set_disabled_limpia_urls(self):
        service = _service()
        service.set_disabled("0", [" http://a ", "", "http://b"])
        assert service._store.saves[0][1].disabled_channels ==             ("http://a", "http://b")

    def test_update_settings(self):
        service = _service()
        out = service.update_settings("0", name="otro", port=9998,
                                      bind="0.0.0.0")
        assert out["ok"] is True
        saved = service._store.saves[0]
        assert saved[1].name == "otro" and saved[1].port == 9998
        assert saved[3] == "0.0.0.0"

    def test_update_settings_port_invalido(self):
        service = _service()
        out = service.update_settings("0", port=70000)
        assert "fuera de rango" in out["error"]
        assert service._store.saves == []

    def test_update_settings_bind_invalido(self):
        service = _service()
        assert "bind" in service.update_settings("0", bind="10.0.0.1")["error"]

    def test_escrituras_sobre_target_inexistente(self):
        service = _service()
        assert "error" in service.set_rules("ghost", [])
        assert "error" in service.set_mappings("ghost", [])
        assert "error" in service.set_disabled("ghost", [])
        assert "error" in service.update_settings("ghost", name="x")

    def test_start_y_stop_service(self):
        service = _service()
        assert service.start_service("0")["ok"] is True
        assert service.stop_service("0")["ok"] is True
        assert service._supervisor.calls[-2:] == [("start", "0"), ("stop", "0")]

    def test_start_service_target_inexistente(self):
        assert "error" in _service().start_service("ghost")


class TestCarteraDeFuentes:
    """La API es la única vía de escritura de ``[source.*]``."""

    def _service(self, tmp_path):
        from src.application.services.daemon.target_store import TargetStore

        path = tmp_path / "config.ini"
        path.write_text(
            "[listmanager.target.0]\nname = t\ncustom_m3u = C:/x.m3u\n"
            "port = 8080\nm3u_sources = \nepg_sources = \n\n"
            "[source.a]\nname = platero\nkind = m3u\nsource_type = file\n"
            "location = C:/platero.m3u\nrefresh_min = 0\nenabled = 1\n",
            encoding="utf-8")
        store = TargetStore(path)
        store.load()
        return TargetService(_StubSupervisor(), store), path

    def test_sources_lee_la_cartera(self, tmp_path):
        service, _ = self._service(tmp_path)
        body = service.sources()
        assert [s["name"] for s in body["sources"]] == ["platero"]

    def test_set_sources_reemplaza_y_conserva_ids(self, tmp_path):
        service, path = self._service(tmp_path)
        out = service.set_sources([
            {"id": "a", "name": "platero", "kind": "m3u",
             "location": "C:/platero.m3u"},
            {"name": "guia", "kind": "epg", "location": "C:/tv.xml"},
        ])
        assert out["ok"] is True and out["errors"] == []
        nombres = sorted(s["name"] for s in out["sources"])
        assert nombres == ["guia", "platero"]
        assert service.sources()["sources"][0]["id"] == "a"
        assert "guia" in path.read_text(encoding="utf-8")

    def test_set_sources_valida_y_no_vacia_la_cartera(self, tmp_path):
        service, _ = self._service(tmp_path)
        out = service.set_sources([{"kind": "otro", "location": "x"}])
        assert out["errors"] and "error" in out
        assert service.sources()["sources"][0]["name"] == "platero"

    def test_set_target_sources_resuelve_refs(self, tmp_path):
        service, _ = self._service(tmp_path)
        out = service.set_target_sources("0", ["a"], [])
        assert out["ok"] is True and out["reloaded"] is True
        target = out["target"]
        assert [r["id"] for r in target["source_refs"]] == ["a"]

    def test_set_target_sources_id_desconocido_falla(self, tmp_path):
        """Mejor 400 que perder la selección en silencio."""
        service, _ = self._service(tmp_path)
        out = service.set_target_sources("0", ["fantasma"], [])
        assert "no existe" in out["error"]

    def test_add_source_valida_la_ubicacion(self, tmp_path):
        service, _ = self._service(tmp_path)
        # el caso que creó la fila "abilio": esquema sin host
        assert "sin host" in service.add_source("x", "m3u", "http://")["error"]
        assert "vacía" in service.add_source("x", "m3u", "")["error"]
        assert "kind" in service.add_source("x", "otro", "C:/a.m3u")["error"]

    def test_add_update_remove_source(self, tmp_path):
        service, _ = self._service(tmp_path)
        added = service.add_source("lista", "m3u", "C:/lista.m3u", 30)
        assert added["refresh_min"] == 30 and added["source_type"] == "file"

        actualizado = service.update_source(added["id"], name="lista v2",
                                            refresh_min=0, enabled=False)
        assert actualizado["source"]["name"] == "lista v2"
        assert actualizado["source"]["enabled"] is False

        assert service.remove_source(added["id"])["ok"] is True
        assert service.remove_source(added["id"])["error"]

    def test_update_source_con_ubicacion_invalida(self, tmp_path):
        service, _ = self._service(tmp_path)
        assert "sin host" in service.update_source("a", location="http://")["error"]
        assert service.sources()["sources"][0]["location"] == "C:/platero.m3u"
