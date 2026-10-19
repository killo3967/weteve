"""Configuración del cliente (``weteve.ini``): dónde y cómo hablar con el demonio.

Es el único fichero INI que queda, y a propósito: son seis claves planas que la
GUI necesita **antes** de que el demonio exista (host, puerto, token y dos
preferencias de arranque). Los datos del demonio viven en ``daemon.toml``.

``mcp_token`` vacío = sin autenticación (solo localhost). Con valor, tanto el
MCP como la API de control exigen ``Authorization: Bearer <token>``.
"""

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DAEMON_SECTION = "DAEMON"

#: Fichero INI del cliente (lo escribe la GUI, lo lee el demonio al arrancar).
CLIENT_FILE = "weteve.ini"

_TRUE = ("1", "true", "yes", "on", "si", "sí")


def _parse_bool(raw: str) -> bool:
    return (raw or "").strip().lower() in _TRUE

@dataclass(frozen=True)
class DaemonConfig:
    """Parámetros del demonio: API de control + endpoint MCP."""

    control_host: str = "127.0.0.1"
    control_port: int = 34400
    mcp_port: int = 34401
    mcp_token: str = ""
    # Arrancar el demonio al abrir la interfaz y pararlo al cerrarla (solo si
    # lo arrancó ella: un demonio del Programador de tareas no se toca).
    autostart: bool = False
    stop_on_exit: bool = False

def load_daemon_config(path: Path) -> DaemonConfig:
    """Lee la sección [DAEMON] de ``weteve.ini``; defaults si no existe.

    Tolera ficheros que no son INI (por ejemplo un ``daemon.toml``): el
    arranque del cliente es opcional y nunca debe impedir abrir la ventana.
    """
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            logger.warning("configuración del cliente ilegible en %s: "
                           "se usan los valores por defecto", path,
                           exc_info=True)
            return DaemonConfig()
    return DaemonConfig(
        control_host=parser.get(DAEMON_SECTION, "control_host", fallback="127.0.0.1"),
        control_port=parser.getint(DAEMON_SECTION, "control_port", fallback=34400),
        mcp_port=parser.getint(DAEMON_SECTION, "mcp_port", fallback=34401),
        mcp_token=parser.get(DAEMON_SECTION, "mcp_token", fallback=""),
        autostart=_parse_bool(parser.get(DAEMON_SECTION, "autostart",
                                         fallback="0")),
        stop_on_exit=_parse_bool(parser.get(DAEMON_SECTION, "stop_on_exit",
                                            fallback="0")),
    )

def save_daemon_config(path: Path, cfg: DaemonConfig) -> None:
    """Escribe [DAEMON] preservando el resto del fichero (targets, etc.)."""
    from src.infrastructure.client.config_store import update, write_daemon

    update(path, lambda parser: write_daemon(parser, cfg))


def write_client_from_legacy(ini_path: Path, client_path: Path) -> None:
    """Migra la sección ``[DAEMON]`` del ``config.ini`` viejo a ``weteve.ini``."""
    save_daemon_config(client_path, load_daemon_config(ini_path))
