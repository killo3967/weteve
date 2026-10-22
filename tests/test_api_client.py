"""Cliente REST de la GUI (ApiClient) contra la API v1 real."""

import pytest

from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.infrastructure.client.api_client import ApiClient
from src.infrastructure.web.rest_api import RestApi


def test_respuesta_que_no_es_json_no_tumba_el_cliente(monkeypatch):
    """Otro servicio en el puerto no puede cerrar la ventana al arrancar.

    Con ``control_host`` apuntando a una máquina donde escucha xTeVe, la API
    responde ``Locked [423]`` en texto plano; antes el ``json.loads`` lanzaba y
    ``StatusPanel.refresh`` mataba la aplicación en su constructor.
    """
    import http.client as http_client

    class _Respuesta:
        status = 423

        def read(self) -> bytes:
            return b"Locked [423]"

    class _Conexion:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def request(self, *args, **kwargs) -> None:
            pass

        def getresponse(self) -> _Respuesta:
            return _Respuesta()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http_client, "HTTPConnection", _Conexion)
    codigo, cuerpo = ApiClient(retries=0).status()
    assert codigo == 0            # "no es el demonio" = no disponible
    assert "no es el demonio" in cuerpo["error"]


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

    def mapping_candidates(self, target_id, limit=200, offset=0,
                           include_served=False):
        self.calls.append(("mapping", include_served))
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

    def test_shutdown_va_por_la_api(self, monkeypatch):
        visto = {}

        def fake_request(self, method, path, payload=None, timeout=None):
            visto.update(method=method, path=path)
            return 200, {"ok": True}

        monkeypatch.setattr(ApiClient, "request", fake_request)
        assert ApiClient().shutdown() == (200, {"ok": True})
        assert (visto["method"], visto["path"]) == ("POST", "/shutdown")

    def test_service_espera_al_pipeline(self, monkeypatch):
        """Arrancar el servicio ejecuta el pipeline: no cabe en 5 s."""
        from src.infrastructure.client.api_client import PIPELINE_TIMEOUT

        visto = {}

        def fake_request(self, method, path, payload=None, timeout=None):
            visto.update(path=path, timeout=timeout)
            return 200, {"ok": True}

        monkeypatch.setattr(ApiClient, "request", fake_request)
        ApiClient().service("0", "start")
        assert visto["path"] == "/targets/0/service"
        assert visto["timeout"] == PIPELINE_TIMEOUT

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


class TestPaginacion:
    """La GUI no puede truncar el catálogo: se piden todas las páginas."""

    def test_epg_channels_recorre_todas_las_paginas(self, monkeypatch) -> None:
        filas = [{"id": f"id{i}"} for i in range(7)]
        pedidos: list[int] = []

        def falso(self, target_id, limit=1000, offset=0):
            pedidos.append(offset)
            page = filas[offset:offset + 3]
            return 200, {"channels": page, "total": len(filas),
                         "count": len(page), "offset": offset}

        monkeypatch.setattr(ApiClient, "epg_channels", falso)
        code, body = ApiClient().all_epg_channels("0")
        assert code == 200
        assert [c["id"] for c in body["channels"]] == [f"id{i}" for i in range(7)]
        assert body["truncated"] is False
        assert pedidos == [0, 3, 6]

    def test_si_el_daemon_no_avanza_se_marca_truncado(self, monkeypatch):
        def falso(self, target_id, limit=1000, offset=0):
            return 200, {"channels": [], "total": 5, "count": 0,
                         "offset": offset}

        monkeypatch.setattr(ApiClient, "epg_channels", falso)
        code, body = ApiClient().all_epg_channels("0")
        assert code == 200 and body["truncated"] is True

    def test_tope_de_paginas_avisa_en_vez_de_colgarse(self, monkeypatch):
        def falso(self, target_id, limit=1000, offset=0):
            return 200, {"channels": [{"id": str(offset)}], "total": 1000,
                         "count": 1, "offset": offset}

        monkeypatch.setattr(ApiClient, "epg_channels", falso)
        code, body = ApiClient().all_epg_channels("0")
        assert code == 200
        assert body["truncated"] is True
        assert len(body["channels"]) == 100      # tope de 100 páginas

    def test_error_en_una_pagina_se_propaga(self, monkeypatch):
        def falso(self, target_id, limit=1000, offset=0):
            return 500, {"error": "boom"}

        monkeypatch.setattr(ApiClient, "epg_channels", falso)
        code, body = ApiClient().all_epg_channels("0")
        assert code == 500 and body["error"] == "boom"
