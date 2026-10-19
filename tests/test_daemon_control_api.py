"""Tests de la API de control HTTP del daemon (JSON, localhost).

La lógica de rutas se testea directamente sobre ``_handle`` (sin sockets,
determinista). Hay un único test de humo HTTP real para validar el arranque,
bind y threading del servidor.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from src.application.services.daemon.control_api import ControlApiServer
from src.application.services.daemon.supervisor import TargetSupervisor


class _StubSupervisor(TargetSupervisor):
    """Supervisor mínimo: evita tocar disco; responde lo que el test necesita."""

    def __init__(self) -> None:
        super().__init__()
        self.reload_calls: list[str] = []

    def get_status(self) -> dict:
        return {
            "targets": {
                "t0": {
                    "name": "Mi lista",
                    "running": True,
                    "port": 8080,
                    "channel_count": 42,
                    "playlist_url": "http://127.0.0.1:8080/playlist.m3u",
                    "epg_url": "http://127.0.0.1:8080/epg.xml",
                    "error": None,
                }
            }
        }

    def reload_target(self, target_id, target=None, epg_path=None) -> bool:
        self.reload_calls.append(target_id)
        return True


@pytest.fixture()
def api() -> ControlApiServer:
    return ControlApiServer(_StubSupervisor(), host="127.0.0.1", port=0)


class TestHandleLogic:
    """Rutas sobre _handle directamente: sin sockets, determinista."""

    def test_health(self, api: ControlApiServer) -> None:
        assert api._handle("GET", "/health", auth_ok=False) == (200, {"ok": True})

    def test_status(self, api: ControlApiServer) -> None:
        status, body = api._handle("GET", "/status", auth_ok=False)
        assert status == 200
        assert body["targets"]["t0"]["running"] is True

    def test_reload(self, api: ControlApiServer) -> None:
        status, body = api._handle("POST", "/targets/t0/reload", auth_ok=False)
        assert status == 200
        assert body["ok"] is True

    def test_reload_unknown_404(self, api: ControlApiServer) -> None:
        status, _ = api._handle("POST", "/targets/ghost/reload", auth_ok=False)
        assert status == 404

    def test_shutdown_calls_callback(self, api: ControlApiServer) -> None:
        called = []
        api._on_shutdown = lambda: called.append(1)
        status, body = api._handle("POST", "/shutdown", auth_ok=False)
        assert status == 200
        assert body["ok"] is True
        assert called == [1]

    def test_unknown_route_404(self, api: ControlApiServer) -> None:
        assert api._handle("GET", "/nope", auth_ok=False)[0] == 404

    def test_token_required(self) -> None:
        api = ControlApiServer(_StubSupervisor(), host="127.0.0.1", port=0,
                               token="s3cr3t")
        assert api._handle("GET", "/status", auth_ok=False)[0] == 401
        assert api._handle("GET", "/status", auth_ok=True)[0] == 200


class TestSmokeHttp:
    """Un test HTTP real: arranque, bind, threading y cierre limpio."""

    def test_log_de_acceso(self, caplog) -> None:
        """Cada petición deja una línea INFO con cliente, ruta y estado."""
        import logging

        sup = _StubSupervisor()
        api = ControlApiServer(sup, host="127.0.0.1", port=0)
        assert api.start() is True
        try:
            with caplog.at_level(logging.INFO):
                for _ in range(40):
                    try:
                        with urllib.request.urlopen(f"{api.url_base}/health",
                                                    timeout=5) as r:
                            assert r.status == 200
                        break
                    except (ConnectionResetError, OSError):
                        time.sleep(0.25)
                else:
                    raise AssertionError("no se pudo conectar")
            mensajes = [rec.getMessage() for rec in caplog.records]
            assert any("control:" in m and "/health" in m and "200" in m
                       for m in mensajes), mensajes
        finally:
            api.stop()

    def test_health_roundtrip(self) -> None:
        sup = _StubSupervisor()
        api = ControlApiServer(sup, host="127.0.0.1", port=0)
        try:
            assert api.start() is True
            # segunda llamada idempotente
            assert api.start() is True

            last: Exception | None = None
            for _ in range(40):
                try:
                    with urllib.request.urlopen(f"{api.url_base}/health",
                                                timeout=5) as r:
                        body = json.loads(r.read().decode("utf-8"))
                    assert body["ok"] is True
                    break
                except (ConnectionResetError, OSError) as e:
                    last = e
                    time.sleep(0.25)
            else:
                raise last  # type: ignore[misc]
        finally:
            api.stop()
            assert api.is_running is False
