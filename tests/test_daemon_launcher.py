"""Tests del DaemonLauncher: arranca el daemon como proceso desasociado.

Sin sockets: se simula el subprocess y solo se valida la construcción del
comando y los flags de desasociación en Windows.
"""

import subprocess
import sys
from pathlib import Path

from src.infrastructure.client.daemon_launcher import DaemonLauncher


class TestDaemonLauncher:
    def test_launch_oculto_por_defecto(self, monkeypatch) -> None:
        """Por defecto NO abre ventana de cmd (logs al fichero)."""
        recorded = {}

        def fake_popen(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["kwargs"] = kwargs

            class P:
                pid = 4242

            return P()

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        launcher = DaemonLauncher()
        ok = launcher.launch()
        assert ok is True
        cmd = recorded["cmd"]
        # intérprete sin consola + daemon.py run
        assert cmd[-1] == "run"
        if sys.platform == "win32":
            # python.exe vuelve a abrir la ventana de cmd: el redirector del
            # venv recrea el proceso y le asigna consola nueva.
            assert Path(cmd[0]).name.lower() == "pythonw.exe"
        else:
            assert cmd[0] == sys.executable
        assert Path(cmd[1]).name == "daemon.py"
        launch_kwargs = recorded["kwargs"]
        assert launch_kwargs["stdout"] is subprocess.DEVNULL
        if sys.platform == "win32":
            flags = launch_kwargs["creationflags"]
            assert flags & subprocess.DETACHED_PROCESS
            assert not flags & subprocess.CREATE_NEW_CONSOLE

    def test_launch_oculto_sin_pythonw(self, monkeypatch) -> None:
        """Sin pythonw.exe: consola oculta (DETACHED_PROCESS sí abre ventana)."""
        recorded = {}

        def fake_popen(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["kwargs"] = kwargs

            class P:
                pid = 7

            return P()

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        monkeypatch.setattr(DaemonLauncher, "_pythonw", lambda self: None)
        assert DaemonLauncher().launch() is True
        assert recorded["cmd"][0] == sys.executable
        if sys.platform == "win32":
            flags = recorded["kwargs"]["creationflags"]
            assert flags & subprocess.CREATE_NO_WINDOW
            assert not flags & subprocess.DETACHED_PROCESS
            assert not flags & subprocess.CREATE_NEW_CONSOLE

    def test_launch_con_consola_explicita(self, monkeypatch) -> None:
        """new_console=True: consola propia con los logs en vivo (opt-in)."""
        recorded = {}

        def fake_popen(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["kwargs"] = kwargs

            class P:
                pid = 1

            return P()

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        DaemonLauncher().launch(new_console=True)
        if sys.platform == "win32":
            # la consola exige python.exe: pythonw no tiene consola
            assert Path(recorded["cmd"][0]).name.lower() == "python.exe"
            assert (recorded["kwargs"]["creationflags"]
                    == subprocess.CREATE_NEW_CONSOLE)

    def test_launch_custom_python(self, monkeypatch) -> None:
        recorded = {}

        def fake_popen(cmd, **kwargs):
            recorded["cmd"] = cmd

            class P:
                pid = 1

            return P()

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        DaemonLauncher(python_path=Path("/otro/python.exe")).launch()
        assert recorded["cmd"][0] == str(Path("/otro/python.exe"))

    def test_launch_failure_returns_false(self, monkeypatch) -> None:
        def fake_popen(cmd, **kwargs):
            raise OSError("no se puede")

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        assert DaemonLauncher().launch() is False
