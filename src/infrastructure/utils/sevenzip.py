"""Extracción de .7z con extractores externos que soportan el filtro BCJ2.

``py7zr`` no soporta el filtro BCJ2 (usado por mpv-winbuild-cmake y otros builds
de binarios), así que la extracción se hace con ``tar.exe`` de Windows (bsdtar,
siempre presente en Windows 10/11) o con 7-Zip si está instalado. Ambos
preservan la estructura de directorios.
"""

import os
import shutil
import subprocess
from pathlib import Path

_EXTRACT_TIMEOUT = 300


class SevenZipExtractError(RuntimeError):
    """Ningún extractor disponible, o todos fallaron."""


def windows_tar_path() -> str | None:
    """Ruta al ``tar.exe`` de Windows (bsdtar), que sí extrae 7z con BCJ2.

    Se usa la ruta explícita del sistema en lugar de ``shutil.which('tar')``
    porque en muchos equipos ese nombre resuelve al GNU tar de Git for Windows,
    que no entiende el formato 7z.
    """
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidate = Path(system_root) / "System32" / "tar.exe"
    return str(candidate) if candidate.is_file() else None


def extract_7z(
    archive: Path,
    dest_dir: Path,
    targets: list[str] | None = None,
) -> None:
    """Extrae ``targets`` (o todo el archivo si ``None``) del .7z en ``dest_dir``.

    Prueba ``tar.exe`` de Windows y luego 7-Zip; ambos soportan BCJ2. Lanza
    ``SevenZipExtractError`` si ninguno está disponible o ambos fallan.
    """
    commands = _build_commands(archive, dest_dir, targets)
    if not commands:
        raise SevenZipExtractError("No hay extractor disponible (tar.exe ni 7-Zip)")
    errors: list[str] = []
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=_EXTRACT_TIMEOUT)
            return
        except (subprocess.SubprocessError, OSError) as e:
            errors.append(f"{Path(cmd[0]).name}: {e}")
    raise SevenZipExtractError(" | ".join(errors))


def _build_commands(
    archive: Path,
    dest_dir: Path,
    targets: list[str] | None,
) -> list[list[str]]:
    """Construye los comandos de extracción disponibles (tar y luego 7-Zip)."""
    commands: list[list[str]] = []
    tar = windows_tar_path()
    if tar is not None:
        cmd = [tar, "-xf", str(archive), "-C", str(dest_dir)]
        cmd += targets or []
        commands.append(cmd)
    seven_zip = shutil.which("7z") or shutil.which("7zr") or shutil.which("7za")
    if seven_zip is not None:
        # "x" (no "e") para preservar la estructura de directorios (p. ej. VLC).
        cmd = [seven_zip, "x", str(archive), f"-o{dest_dir}"]
        if targets:
            cmd += targets
        cmd.append("-y")
        commands.append(cmd)
    return commands
