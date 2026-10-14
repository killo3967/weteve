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


class TestSetupLogging:
    """Log dual: consola + fichero logs/weteve-daemon.log."""

    def test_configura_console_y_file(self, tmp_path) -> None:
        import logging
        from logging.handlers import RotatingFileHandler

        log_path = tmp_path / "weteve-daemon.log"
        daemon_mod.setup_logging(log_path)
        root = logging.getLogger()
        handlers = list(root.handlers)
        types = [type(h) for h in handlers]
        assert logging.StreamHandler in types
        assert logging.handlers.RotatingFileHandler in types
        # el fichero recibe registros
        logging.getLogger("test").info("hola log")
        for h in handlers:
            h.flush()
        assert log_path.exists()

    def test_no_duplica_handlers(self, tmp_path) -> None:
        daemon_mod.setup_logging(tmp_path / "a.log")
        import logging
        n = len(logging.getLogger().handlers)
        daemon_mod.setup_logging(tmp_path / "a.log")
        assert len(logging.getLogger().handlers) == n

    def test_default_log_dir_junto_al_repo(self) -> None:
        p = daemon_mod.default_log_path()
        assert p.parent.name == "logs"
        assert p.name == "weteve-daemon.log"

    def test_etiqueta_de_nivel_en_fichero(self, tmp_path) -> None:
        """El log del daemon marca el nivel: ``[info]``, ``[warn]``…

        Se aísla de otros tests del módulo quitando los handlers del root
        (setup_logging es idempotente y no reconfiguraría).
        """
        import logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        log_path = tmp_path / "nivel.log"
        for h in saved_handlers:
            root.removeHandler(h)
        try:
            daemon_mod.setup_logging(log_path)
            logging.getLogger("test").info("arrancado")
            logging.getLogger("test").warning("puerto ocupado")
            for h in root.handlers:
                h.flush()
            contenido = log_path.read_text(encoding="utf-8")
            assert "[info] test: arrancado" in contenido
            assert "[warn] test: puerto ocupado" in contenido
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in saved_handlers:
                root.addHandler(h)
            root.setLevel(saved_level)
