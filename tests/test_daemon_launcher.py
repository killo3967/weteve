"""Tests del DaemonLauncher: arranca el daemon como proceso desasociado.

Sin sockets: se simula el subprocess y solo se valida la construcción del
comando y los flags de desasociación en Windows.
"""

import subprocess
import sys
from pathlib import Path

from src.infrastructure.client.daemon_launcher import DaemonLauncher


class TestDaemonLauncher:
    def test_launch_new_console_flags(self, monkeypatch) -> None:
        """Por defecto abre consola propia (logs visibles)."""
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
        # python + daemon.py run
        assert cmd[-1] == "run"
        assert cmd[0] == sys.executable
        assert Path(cmd[1]).name == "daemon.py"
        kwargs = recorded["kwargs"]
        assert kwargs["stdout"] is not None  # DEVNULL: la consola muestra los logs
        if sys.platform == "win32":
            assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE

    def test_launch_detached_when_no_console(self, monkeypatch) -> None:
        """new_console=False: proceso oculto (arranque automático, sin ventana)."""
        recorded = {}

        def fake_popen(cmd, **kwargs):
            recorded["kwargs"] = kwargs

            class P:
                pid = 1

            return P()

        monkeypatch.setattr("src.infrastructure.client.daemon_launcher.subprocess.Popen",
                            fake_popen)
        DaemonLauncher().launch(new_console=False)
        if sys.platform == "win32":
            flags = recorded["kwargs"]["creationflags"]
            assert flags & subprocess.DETACHED_PROCESS
            assert flags & subprocess.CREATE_NEW_PROCESS_GROUP

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
