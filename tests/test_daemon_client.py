"""Tests del DaemonClient (cliente HTTP de la API de control).

Testeado contra un ControlApiServer real sobre un stub del supervisor:
solo hay que validar mapeo de métodos HTTP → respuestas y errores.
"""

import pytest

from src.application.services.daemon.control_api import ControlApiServer
from src.application.services.daemon.supervisor import TargetSupervisor
from src.infrastructure.client.daemon_client import DaemonClient


class _StubSupervisor(TargetSupervisor):
    def get_status(self) -> dict:
        return {"targets": {"t0": {"name": "A", "running": True}}}

    def reload_target(self, target_id, target=None, epg_path=None) -> bool:
        return target_id == "t0"


@pytest.fixture()
def server(_unused_port=None):
    sup = _StubSupervisor()
    api = ControlApiServer(sup, host="127.0.0.1", port=0)
    assert api.start() is True
    yield api
    api.stop()


@pytest.fixture()
def client(server) -> DaemonClient:
    return DaemonClient(host=server.host, port=server.port, retries=10, retry_delay=0.3)


def _retry(fn, attempts: int = 8):
    """Reintentos para el flake Windows de RST en loopback."""
    import time
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.25)
    raise last  # type: ignore[misc]


class TestDaemonClient:
    def test_ping(self, client: DaemonClient) -> None:
        assert _retry(lambda: client.ping()) is True

    def test_status(self, client: DaemonClient) -> None:
        status = _retry(lambda: client.status())
        assert status["targets"]["t0"]["running"] is True

    def test_reload_target(self, client: DaemonClient) -> None:
        assert _retry(lambda: client.reload_target("t0")) is True

    def test_reload_unknown_false(self, client: DaemonClient) -> None:
        assert _retry(lambda: client.reload_target("ghost")) is False

    def test_ping_sin_daemon_false(self) -> None:
        # puerto casi seguro sin listener
        c = DaemonClient(host="127.0.0.1", port=1)
        assert c.ping() is False

    def test_fallo_de_conexion_queda_en_el_log(self, caplog) -> None:
        """Antes era silencioso: la UI daba error y el log no decía nada."""
        import logging

        c = DaemonClient(host="127.0.0.1", port=1, retries=1,
                         retry_delay=0.01)
        with caplog.at_level(
                logging.WARNING,
                logger="src.infrastructure.client.daemon_client"):
            assert c.ping() is False
        assert any("sin respuesta" in rec.getMessage()
                   for rec in caplog.records), \
            [rec.getMessage() for rec in caplog.records]

    def test_http_error_queda_en_el_log(self, caplog, server) -> None:
        import logging
        import time

        api = ControlApiServer(_StubSupervisor(), host="127.0.0.1", port=0,
                               token="tk")
        assert api.start() is True
        try:
            c = DaemonClient(host=api.host, port=api.port, retries=5,
                             retry_delay=0.2)
            with caplog.at_level(
                    logging.WARNING,
                    logger="src.infrastructure.client.daemon_client"):
                # el primer intento puede caer en un RST de loopback (Windows)
                for _ in range(8):
                    caplog.clear()
                    assert c.ping() is False
                    if any("HTTP 401" in rec.getMessage()
                           for rec in caplog.records):
                        break
                    time.sleep(0.25)
            assert any("HTTP 401" in rec.getMessage()
                       for rec in caplog.records), \
                [rec.getMessage() for rec in caplog.records]
        finally:
            api.stop()

    def test_token(self, server) -> None:
        from src.application.services.daemon.control_api import ControlApiServer as C
        sup = _StubSupervisor()
        api = C(sup, host="127.0.0.1", port=0, token="tk")
        assert api.start() is True
        try:
            sin_token = DaemonClient(host=api.host, port=api.port, retries=10, retry_delay=0.3)
            con_token = DaemonClient(host=api.host, port=api.port, token="tk", retries=10, retry_delay=0.3)
            assert sin_token.ping() is False
            assert _retry(lambda: con_token.ping()) is True
        finally:
            api.stop()
