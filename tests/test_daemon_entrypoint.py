"""Tests del ensamblado del daemon (daemon.py).

Valida el wiring: carga config, construye supervisor + store + control API +
MCP y arranca todo. Sin sockets de verdad: se inyectan los servidores.
"""

import pytest

import daemon as daemon_mod
from src.application.services.daemon.config import DaemonConfig


class _FakeServer:
    """Sustituto de ControlApiServer / McpHttpTransport para tests."""

    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self.stopped = False
        self.url_base = "http://fake"

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    cfg = DaemonConfig(control_host="127.0.0.1", control_port=0,
                       mcp_port=0, mcp_token="")
    monkeypatch.setattr(daemon_mod, "load_daemon_config",
                        lambda path: cfg)
    monkeypatch.setattr(daemon_mod, "ControlApiServer", _FakeServer)
    monkeypatch.setattr(daemon_mod, "McpHttpTransport", _FakeServer)
    return daemon_mod.build_daemon(config_path=tmp_path / "config.ini")


class TestBuildDaemon:
    def test_wiring(self, wired) -> None:
        d = wired
        assert d["supervisor"] is not None
        assert d["store"] is not None
        assert d["control_api"].started is True
        assert d["mcp_transport"].started is True
        assert d["running"]["flag"] is True

    def test_tools_registered(self, wired) -> None:
        # el transporte fake no guarda el core; comprobamos por el registro
        # reconstruido con los mismos componentes
        d = wired
        from src.application.services.daemon.mcp_tools import build_tool_registry
        reg = build_tool_registry(d["supervisor"], d["store"],
                                  on_shutdown=d["shutdown"])
        assert "create_target" in reg and "get_status" in reg

    def test_shutdown_flag(self, wired) -> None:
        d = wired
        d["shutdown"]()
        assert d["running"]["flag"] is False


class TestCli:
    def test_status_sin_daemon(self, capsys) -> None:
        rc = daemon_mod.cmd_status(port=1)
        assert rc == 1
        assert "no responde" in capsys.readouterr().out.lower()

    def test_stop_sin_daemon(self, capsys) -> None:
        rc = daemon_mod.cmd_stop(port=1)
        assert rc == 1
