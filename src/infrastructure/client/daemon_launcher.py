"""Lanzador de procesos del daemon: arrancar el servicio desde la GUI.

La GUI no posee el daemon: lo lanza como proceso desasociado (sin consola
en Windows) y su ciclo de vida posterior es del propio proceso vía la API
de control (/shutdown). Detener = pedir shutdown por HTTP, no matar PID.
"""

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

def _daemon_script() -> Path:
    """Ruta a daemon.py relativa a este paquete (src/infrastructure/client)."""
    return Path(__file__).resolve().parents[3] / "daemon.py"

class DaemonLauncher:
    """Lanza `python daemon.py run` como proceso desasociado."""

    def __init__(self, python_path: Path | None = None,
                 script_path: Path | None = None) -> None:
        self._python = python_path or Path(sys.executable)
        self._script = script_path or _daemon_script()

    def _build_cmd(self) -> list[str]:
        return [str(self._python), str(self._script), "run"]

    def _pythonw(self) -> Path | None:
        """`pythonw.exe` hermano del intérprete, si existe.

        Con `python.exe` no basta para no abrir ventana: el redirector del
        venv recrea el proceso real y no propaga `DETACHED_PROCESS`, así que
        el proceso hijo se queda sin consola y Windows le asigna una nueva
        (la ventana de cmd). `pythonw.exe` es subsistema GUI: no tiene
        consola nunca.
        """
        if sys.platform != "win32":
            return None
        pythonw = self._python.with_name("pythonw.exe")
        return pythonw if pythonw.exists() else None

    def launch(self, new_console: bool = False) -> bool:
        """Arranca el daemon.

        Args:
            new_console: abrir una consola propia con los logs en vivo.
                Por defecto ``False``: proceso oculto sin ventana de cmd (los
                logs van a ``logs/weteve-daemon.log``). En no-Windows se ignora.
        """
        cmd = self._build_cmd()
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            pythonw = self._pythonw()
            if new_console:
                # Consola propia: los logs del daemon se ven en vivo.
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            elif pythonw is not None:
                # Sin consola: pythonw + proceso desasociado.
                cmd[0] = str(pythonw)
                kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                           | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                # Sin pythonw: al menos consola oculta (nunca DETACHED aquí).
                kwargs["creationflags"] = (subprocess.CREATE_NO_WINDOW
                                           | subprocess.CREATE_NEW_PROCESS_GROUP)
            kwargs["close_fds"] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603
            logger.info("daemon lanzado (pid %s)", proc.pid)
            return True
        except OSError as e:
            logger.error("no se pudo lanzar el daemon: %s", e)
            return False

    @staticmethod
    def stop_via_client(client) -> bool:
        """Parada elegante vía API de control (shutdown)."""
        return client.shutdown()
