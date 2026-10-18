"""Cliente REST de la GUI (ApiClient) contra la API v1 real."""

import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.infrastructure.client.api_client import ApiClient
from src.infrastructure.web.rest_api import RestApi


class _FakeService:
    def __init__(self) -> None:
        self.running = False
        self.calls: list[tuple] = []

    def status(self) -> dict:
        return {"targets": {"0": {"name": "test", "running": self.running}}}

    def list_targets(self) -> dict:
        return {"targets": {"0": {"name": "test"}}}

    def get_target(self, target_id: str):
        return {"target": {"name": "test"}} if target_id == "0" else None

    def runtime_status(self, target_id: str):
        return {"name": "test", "running": self.running, "port": 9999}

    def channels(self, target_id, limit=50, offset=0, unmatched_only=False):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def mapping_candidates(self, target_id, limit=200, offset=0):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def epg_channels(self, target_id, limit=200, offset=0):
        return {"channels": [], "total": 0, "count": 0, "offset": offset}

    def update_settings(self, target_id, **kw):
        self.calls.append(("settings", target_id, kw))
        return {"ok": True, "target": {"port": kw.get("port")}}

    def set_rules(self, target_id, rules, reload=True):
        self.calls.append(("rules", target_id, rules))
        return {"ok": True, "reloaded": reload}

    def set_mappings(self, target_id, mappings, reload=True):
        return {"ok": True, "applied": mappings, "errors": []}

    def set_disabled(self, target_id, urls, reload=True):
        return {"ok": True}

    def set_target_sources(self, target_id, m3u_ids, epg_ids, reload=True):
        self.calls.append(("sources", target_id, m3u_ids, epg_ids, reload))
        return {"ok": True, "target": {"source_refs": []}}

    def run(self, target_id):
        return {"filtered_count": 2}

    def start_service(self, target_id):
        self.calls.append(("start", target_id))
        self.running = True
        return {"ok": True, "status": self.runtime_status(target_id)}

    def stop_service(self, target_id):
        self.calls.append(("stop", target_id))
        self.running = False
        return {"ok": True, "status": self.runtime_status(target_id)}


@pytest.fixture
def api():
    service = _FakeService()
    transport = McpHttpTransport(McpStreamableServer(), port=0,
                                 rest_api=RestApi(service))
    assert transport.start()
    yield ApiClient(port=transport.port), service
    transport.stop()


class TestApiClient:
    def test_status(self, api):
        client, service = api
        code, body = client.status()
        assert code == 200 and body["targets"]["0"]["name"] == "test"

    def test_target_status(self, api):
        client, _ = api
        assert client.target_status("0")[1]["running"] is False
        assert client.target_status("9")[0] == 404

    def test_servicio_start_y_stop(self, api):
        client, service = api
        assert client.service("0", "start")[1]["ok"] is True
        assert service.running is True
        assert client.target_status("0")[1]["running"] is True
        assert client.service("0", "stop")[1]["ok"] is True
        assert service.running is False

    def test_settings(self, api):
        client, service = api
        assert client.set_settings("0", port=9998, bind="127.0.0.1")[0] == 200
        kind, target_id, kwargs = service.calls[-1]
        assert (kind, target_id) == ("settings", "0")
        assert kwargs["port"] == 9998 and kwargs["bind"] == "127.0.0.1"

    def test_run(self, api):
        client, _ = api
        assert client.run("0")[1]["filtered_count"] == 2

    def test_run_espera_al_pipeline(self, monkeypatch):
        """El pipeline tarda segundos: el `run` no corta al timeout corto."""
        from src.infrastructure.client.api_client import PIPELINE_TIMEOUT

        visto = {}

        def fake_request(self, method, path, payload=None, timeout=None):
            visto.update(method=method, path=path, timeout=timeout)
            return 200, {}

        monkeypatch.setattr(ApiClient, "request", fake_request)
        ApiClient().run("0")
        assert visto["path"] == "/targets/0/run"
        assert visto["timeout"] == PIPELINE_TIMEOUT
        assert PIPELINE_TIMEOUT >= 60

    def test_set_target_sources_sin_recargar(self, api):
        """En el lote de guardado no se recarga: solo el `run` final ejecuta."""
        client, service = api
        code, _ = client.set_target_sources("0", ["a"], ["b"], reload=False)
        assert code == 200
        assert service.calls[-1] == ("sources", "0", ["a"], ["b"], False)

    def test_daemon_caido_no_lanza(self):
        client = ApiClient(port=1, timeout=0.2)
        code, body = client.status()
        assert code == 0 and "error" in body


class TestFastFail:
    """Con el demonio caído no se paga el rechazo de ~2 s en cada consulta."""

    def test_tras_un_rechazo_no_reintenta(self):
        import time

        client = ApiClient(port=1, timeout=0.2)
        code, body = client.request("GET", "/health")
        assert code == 0 and "error" in body
        assert client._down_until > time.monotonic()

        t0 = time.perf_counter()
        code, body = client.request("GET", "/health")
        elapsed = time.perf_counter() - t0
        assert code == 0
        assert "intento reciente" in body["error"]
        assert elapsed < 0.01, f"el segundo intento esperó {elapsed:.3f}s"

    def test_un_acierto_limpia_el_estado_caido(self):
        transport = McpHttpTransport(McpStreamableServer(), port=0,
                                    rest_api=RestApi(_FakeService()))
        assert transport.start()
        try:
            client = ApiClient(port=transport.port)
            client._down_until = 0.0
            assert client.request("GET", "/health")[0] == 200
            assert client._down_until == 0.0
        finally:
            transport.stop()
