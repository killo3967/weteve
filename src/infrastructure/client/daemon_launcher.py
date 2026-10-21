"""Lanzador de procesos del daemon: arrancar el servicio desde la GUI.

La GUI no posee el daemon: lo lanza como proceso desasociado (sin consola
en Windows) y su ciclo de vida posterior es del propio proceso vía la API
de control (/shutdown). Detener = pedir shutdown por HTTP, no matar PID.
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

def _terminar(pid: int) -> None:
    """Termina un proceso por PID (taskkill en Windows)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, check=False)
        return
    import os
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


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

    def start_and_wait(self, client, timeout: float = 12.0) -> tuple[bool, str]:
        """Lanza el demonio y espera a que responda. Devuelve (ok, motivo).

        ``launch()`` solo sabe si el proceso se creó: si el hijo muere al
        instante (token obligatorio, puerto ocupado, dirección que no es de
        este equipo) la interfaz se quedaba diciendo «arrancando…» para
        siempre. Aquí se comprueba el arranque de verdad y, si falla, se
        devuelve la última línea del log del demonio, que es el motivo.
        """
        if not self.launch():
            return False, "no se pudo lanzar el proceso (ver logs)"
        fin = time.monotonic() + float(timeout)
        while time.monotonic() < fin:
            if client.ping():
                return True, ""
            time.sleep(0.25)
        return False, self.last_log_line() or "no responde tras el arranque"

    @staticmethod
    def last_log_line() -> str:
        """Última línea del log del demonio, sin fecha ni nivel."""
        ruta = _daemon_script().parent / "logs" / "weteve-daemon.log"
        try:
            lineas = [linea for linea in
                      ruta.read_text(encoding="utf-8", errors="ignore").splitlines()
                      if linea.strip()]
        except OSError:
            return ""
        if not lineas:
            return ""
        partes = lineas[-1].split("] ", 1)
        return partes[-1] if len(partes) == 2 else lineas[-1]

    @staticmethod
    def daemon_address() -> str | None:
        """Dirección del demonio ya en marcha según su candado, si lo hay.

        El ping de la GUI va al puerto configurado: si el que corre escucha en
        otro, lanzaría un segundo. El candado dice dónde está de verdad.
        """
        from src.infrastructure.client.single_instance import (
            held,
            lock_path,
            pid_path,
            read_address,
        )

        config = _daemon_script().parent / "daemon.toml"
        if not held(lock_path(config)):
            return None
        return read_address(pid_path(config))

    def stop_running(self, timeout: float = 8.0) -> bool:
        """Para el demonio que tiene el candado, por PID.

        Es el último recurso cuando el ``shutdown`` por HTTP no vale: cambiar
        el token deja al demonio vivo sin aceptar órdenes (401) y, con el
        candado, tampoco se puede arrancar otro. Aquí se lee su PID del fichero
        y se termina; el candado lo libera el sistema al morir.
        """
        from src.infrastructure.client.single_instance import (
            held,
            lock_path,
            pid_path,
            read_pid,
        )

        config = _daemon_script().parent / "daemon.toml"
        if not held(lock_path(config)):
            return True                       # no hay nadie a quien parar
        pid = read_pid(pid_path(config))
        if pid is None:
            return False
        _terminar(pid)
        fin = time.monotonic() + float(timeout)
        while time.monotonic() < fin:
            if not held(lock_path(config)):
                return True
            time.sleep(0.2)
        logger.warning("el demonio %s sigue vivo tras terminarlo", pid)
        return False

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
