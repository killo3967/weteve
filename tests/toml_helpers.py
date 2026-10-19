"""Ayudas para escribir ``daemon.toml`` / ``weteve.ini`` en los tests.

Todo lo que era ``config.ini`` se reparte ahora en dos ficheros: los datos del
demonio (TOML) y el arranque del cliente (INI). Estas funciones evitan que cada
test reconstruya el esquema a mano.
"""

from pathlib import Path

from src.application.services.daemon.config import (
    CLIENT_FILE,
    DaemonConfig,
    save_daemon_config,
)
from src.infrastructure.client import config_toml


def target(target_id: str = "0", **extra) -> dict:
    """Bloque ``[[targets]]`` con los valores por defecto del esquema."""
    base = {
        "id": str(target_id),
        "name": "t",
        "port": 8080,
        "bind": "127.0.0.1",
        "starting_number": 1,
        "custom_m3u": "",
        "custom_epg": "",
        "epg_source": -1,
        "m3u_sources": [],
        "epg_sources": [],
        "rules": [],
        "disabled": [],
        "overrides": {},
        "quality_priority": {},
    }
    base.update(extra)
    return base


def source(source_id: str = "s1", name: str = "lista", kind: str = "m3u",
           location: str = "C:/l.m3u", **extra) -> dict:
    """Bloque ``[[sources]]``."""
    base = {"id": source_id, "name": name, "kind": kind,
            "source_type": "url" if location.startswith("http") else "file",
            "location": location, "refresh_min": 0, "enabled": True}
    base.update(extra)
    return base


def write_daemon(path: Path, targets=(), sources=()) -> Path:
    """Escribe ``daemon.toml`` con los targets y fuentes dados."""
    config_toml.save(path, {"version": config_toml.SCHEMA_VERSION,
                            "sources": list(sources),
                            "targets": list(targets)})
    return path


def write_client(path: Path, **extra) -> Path:
    """Escribe ``weteve.ini`` con la configuración de arranque indicada."""
    valores = {"control_host": "127.0.0.1", "control_port": 34400,
               "mcp_port": 34400, "mcp_token": "", "autostart": False,
               "stop_on_exit": False}
    valores.update(extra)
    save_daemon_config(path, DaemonConfig(**valores))
    return path


def patch_daemon(path: Path, mutate) -> dict:
    """Lee, muta y reescribe ``daemon.toml`` (para retocar un test)."""
    config = config_toml.load(path)
    mutate(config)
    config_toml.save(path, config)
    return config


__all__ = ["CLIENT_FILE", "patch_daemon", "source", "target", "write_client",
           "write_daemon"]
