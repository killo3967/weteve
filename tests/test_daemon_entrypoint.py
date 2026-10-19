"""Tests del ensamblado del daemon (daemon.py).

Valida el wiring: carga config, construye supervisor + store + control API +
MCP y arranca todo. Sin sockets de verdad: se inyectan los servidores.
"""

import pytest

import daemon as daemon_mod
from src.application.services.daemon.config import DaemonConfig


class _FakeServer:
    """Sustituto del servidor único del demonio para tests."""

    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self.stopped = False
        self.port = 34400
        self.url_base = "http://fake"

    @property
    def is_running(self) -> bool:
        return self.started

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
    return daemon_mod.build_daemon(
        config_path=tmp_path / "daemon.toml",
        client_path=tmp_path / "weteve.ini")


class TestBuildDaemon:
    def test_wiring(self, wired) -> None:
        d = wired
        assert d["supervisor"] is not None
        assert d["store"] is not None
        assert d["server"].is_running is True
        assert d["control_api"] is not None  # handler sin socket
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


class TestTokenObligatorioFueraDeLoopback:
    def test_sin_token_y_bind_publico_no_arranca(self, tmp_path):
        import pytest as _pytest
        from toml_helpers import write_client

        import daemon

        cfg = write_client(tmp_path / "weteve.ini", control_host="0.0.0.0",
                           control_port=0, mcp_port=0, mcp_token="")
        with _pytest.raises(RuntimeError, match="mcp_token obligatorio"):
            daemon.build_daemon(tmp_path / "daemon.toml", cfg)

    def test_con_token_si_arranca(self, tmp_path):
        from toml_helpers import write_client

        import daemon

        cfg = write_client(tmp_path / "weteve.ini", control_host="0.0.0.0",
                           control_port=0, mcp_port=0, mcp_token="tk")
        d = daemon.build_daemon(tmp_path / "daemon.toml", cfg)
        try:
            assert d["server"].is_running
        finally:
            d["supervisor"].stop_all()
            d["server"].stop()


class TestInstallTask:
    """schtasks se invoca con argv: sin shell y sin comillas anidadas."""

    def test_sin_shell_y_con_argv(self, monkeypatch):
        visto = {}

        def fake_run(argv, **kwargs):
            visto["argv"] = argv
            visto["kwargs"] = kwargs
            return type("R", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert daemon_mod.cmd_install_task() == 0
        assert isinstance(visto["argv"], list)
        assert visto["argv"][0] == "schtasks"
        assert "shell" not in visto["kwargs"]
        # el comando de la tarea va entero en un solo argumento, entre comillas
        i = visto["argv"].index("/TR")
        assert visto["argv"][i + 1].endswith("run")
        assert visto["argv"][i + 1].count('"') == 4
        assert "/TN" in visto["argv"]
        assert visto["argv"][visto["argv"].index("/TN") + 1] == "weteve-daemon"

    def test_error_de_schtasks_devuelve_1(self, monkeypatch, capsys):
        def fake_run(argv, **kwargs):
            return type("R", (), {"returncode": 1, "stderr": "acceso denegado"})()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert daemon_mod.cmd_install_task() == 1
        assert "acceso denegado" in capsys.readouterr().out


class TestCliLeeElFicheroDelCliente:
    """`daemon.py status/stop` usan el puerto de ``weteve.ini``, no 34400 fijo."""

    def test_status_usa_el_puerto_configurado(self, tmp_path, monkeypatch):
        from toml_helpers import write_client

        cfg = write_client(tmp_path / "weteve.ini", control_port=34990,
                           mcp_token="tk")
        visto = {}

        class _FakeClient:
            def __init__(self, host="127.0.0.1", port=34400, token="", **_kw):
                visto.update(host=host, port=port, token=token)

            def status(self):
                return {"targets": {}}

        monkeypatch.setattr(daemon_mod, "DaemonClient", _FakeClient)
        assert daemon_mod.cmd_status(client_path=cfg) == 0
        assert visto == {"host": "127.0.0.1", "port": 34990, "token": "tk"}

    def test_stop_usa_el_puerto_configurado(self, tmp_path, monkeypatch):
        from toml_helpers import write_client

        cfg = write_client(tmp_path / "weteve.ini", control_port=34991)
        visto = {}

        class _FakeClient:
            def __init__(self, host="127.0.0.1", port=34400, token="", **_kw):
                visto["port"] = port

            def shutdown(self):
                return True

        monkeypatch.setattr(daemon_mod, "DaemonClient", _FakeClient)
        assert daemon_mod.cmd_stop(client_path=cfg) == 0
        assert visto["port"] == 34991

    def test_el_puerto_explicito_manda(self, tmp_path, monkeypatch):
        from toml_helpers import write_client

        cfg = write_client(tmp_path / "weteve.ini", control_port=34990)
        visto = {}

        class _FakeClient:
            def __init__(self, host="127.0.0.1", port=34400, token="", **_kw):
                visto["port"] = port

            def status(self):
                return {"targets": {}}

        monkeypatch.setattr(daemon_mod, "DaemonClient", _FakeClient)
        daemon_mod.cmd_status(port=1234, client_path=cfg)
        assert visto["port"] == 1234
