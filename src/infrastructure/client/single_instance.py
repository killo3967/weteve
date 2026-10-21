"""Candado de instancia única del demonio.

El demonio escribe ``daemon.toml`` y sirve un solo puerto: dos procesos a la vez
se pisan los guardados. Hasta ahora el único candado era el ``bind`` del puerto,
así que dos demonios con distinto ``control_port`` convivían sin problema y el
segundo se comía los cambios del primero.

Aquí se toman dos ficheros junto a ``daemon.toml`` (el recurso compartido) y
**antes** de leer la configuración:

- ``weteve.lock``: el bloqueo del sistema operativo. No lleva contenido; lo
  libera el sistema si el proceso muere, así que no hay que limpiar nada.
- ``weteve.pid``: ``pid host:puerto`` en texto plano, para que la GUI (y
  cualquiera con un editor) sepa quién corre y dónde. Va aparte a propósito: en
  Windows un bloqueo por rango impide leer esos bytes, y un ``.pid`` que no se
  puede abrir no sirve de nada.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO

LOCK_NAME = "weteve.lock"
PID_NAME = "weteve.pid"


def lock_path(config_path: Path | str) -> Path:
    """Fichero de bloqueo que corresponde a un ``daemon.toml`` dado."""
    return Path(config_path).resolve().parent / LOCK_NAME


def pid_path(config_path: Path | str) -> Path:
    """Fichero con ``pid host:puerto`` del demonio que tiene el candado."""
    return Path(config_path).resolve().parent / PID_NAME


class DaemonLock:
    """Bloqueo exclusivo y no bloqueante sobre el fichero de instancia."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._file: TextIO | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> bool:
        """Toma el candado. False si ya lo tiene otro proceso."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = open(self._path, "a+", encoding="utf-8")
        except OSError:
            return False
        # El fichero necesita un byte que bloquear. Se mira el tamaño, no el
        # contenido: leer el byte bloqueado por otro proceso da PermissionError.
        try:
            if os.path.getsize(self._path) == 0:
                handle.write(" ")
                handle.flush()
        except OSError:
            pass
        handle.seek(0)
        if not _bloquear(handle):
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        """Suelta el candado. Los ficheros se conservan."""
        handle = self._file
        self._file = None
        if handle is None:
            return
        try:
            _soltar(handle)
        finally:
            handle.close()


def _bloquear(handle) -> bool:
    """Bloqueo no bloqueante del primer byte (falla si lo tiene otro)."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _soltar(handle) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def write_pid(path: Path | str, address: str) -> None:
    """Deja escrito quién es el demonio y dónde escucha."""
    ruta = Path(path)
    ruta.write_text(f"{os.getpid()} {address}\n", encoding="utf-8")


def read_address(path: Path | str) -> str | None:
    """Dirección anotada en el fichero de PID (``host:puerto``), si la hay."""
    try:
        campos = Path(path).read_text(encoding="utf-8", errors="ignore").split()
    except OSError:
        return None
    return campos[1] if len(campos) >= 2 else None


def read_pid(path: Path | str) -> int | None:
    """PID anotado en el fichero del demonio en marcha."""
    try:
        campos = Path(path).read_text(encoding="utf-8", errors="ignore").split()
    except OSError:
        return None
    try:
        return int(campos[0]) if campos else None
    except ValueError:
        return None


def held(path: Path | str) -> bool:
    """True si otro proceso tiene tomado el candado ahora mismo."""
    ruta = Path(path)
    if not ruta.exists():
        return False
    candado = DaemonLock(ruta)
    if not candado.acquire():
        return True
    candado.release()
    return False
