"""Configuración [DAEMON] de weteve en config.ini.

Sección independiente de ``listmanager.target.N``; el guardado preserva las
secciones existentes. Claves: control_host, control_port, mcp_port, mcp_token.

``mcp_token`` vacío = sin autenticación (solo localhost). Con valor, tanto el
MCP como la API de control exigen ``Authorization: Bearer <token>``.
"""

import configparser
from dataclasses import dataclass
from pathlib import Path

DAEMON_SECTION = "DAEMON"

@dataclass(frozen=True)
class DaemonConfig:
    """Parámetros del demonio: API de control + endpoint MCP."""

    control_host: str = "127.0.0.1"
    control_port: int = 34400
    mcp_port: int = 34401
    mcp_token: str = ""

def load_daemon_config(path: Path) -> DaemonConfig:
    """Lee la sección [DAEMON] de config.ini; defaults si no existe."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    return DaemonConfig(
        control_host=parser.get(DAEMON_SECTION, "control_host", fallback="127.0.0.1"),
        control_port=parser.getint(DAEMON_SECTION, "control_port", fallback=34400),
        mcp_port=parser.getint(DAEMON_SECTION, "mcp_port", fallback=34401),
        mcp_token=parser.get(DAEMON_SECTION, "mcp_token", fallback=""),
    )

def save_daemon_config(path: Path, cfg: DaemonConfig) -> None:
    """Escribe [DAEMON] preservando el resto del fichero (targets, etc.)."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    parser[DAEMON_SECTION] = {
        "control_host": cfg.control_host,
        "control_port": str(cfg.control_port),
        "mcp_port": str(cfg.mcp_port),
        "mcp_token": cfg.mcp_token,
    }
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)
