"""Lanzador de procesos del daemon: arrancar el servicio desde la GUI.

La GUI no posee el daemon: lo lanza como proceso desasociado (sin consola
en Windows) y su ciclo de vida posterior es del propio proceso vía la API
de control (/shutdown). Detener = pedir shutdown por HTTP, no matar PID.
"""

import logging
import os
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

    def launch(self) -> bool:
        """Arranca el daemon. False si el proceso no pudo crearse."""
        cmd = [str(self._python), str(self._script), "run"]
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # DETACHED_PROCESS: sin consola; CREATE_NEW_PROCESS_GROUP: aislado
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
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
