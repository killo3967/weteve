"""Configuración de weteve en TOML: esquema, lectura y escritura.

Un solo codec para dos ficheros, cada uno con un único escritor:

- ``config.toml``: lo escribe **el demonio** (cartera de fuentes, targets,
  reglas, mapeos, canales desactivados y prioridad de calidad).
- ``daemon.toml``: lo escribe **la GUI** (host, puerto, token de arranque).

Ventajas frente al viejo ``config.ini``: tipos nativos (enteros y booleanos no
son cadenas), arrays para listas (reglas, desactivados, selección de fuentes) y
**claves entrecomilladas** para los mapeos url → channel-id, que en INI era
imposible porque configparser parte las opciones por ``=`` y ``:``.

TOML se lee con ``tomllib`` (stdlib); la escritura es propia para no añadir
dependencias: solo se emiten los tipos del esquema (cadena, entero, booleano y
arrays de cadenas, tablas y arrays de tablas).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from src.infrastructure.client.config_store import write_atomic

#: Versión del esquema. Un fichero con versión mayor no se toca: se avisa.
SCHEMA_VERSION = 2

#: Nombres de fichero por defecto (junto al ejecutable / config.ini antiguo).
CONFIG_FILE = "config.toml"
DAEMON_FILE = "daemon.toml"


class ConfigError(RuntimeError):
    """Fichero con una versión de esquema que este binario no entiende."""


# -- escritura -----------------------------------------------------------------

def _str(value: str) -> str:
    """Cadena TOML entre comillas dobles, con los escapes necesarios."""
    out = (str(value).replace("\\", "\\\\").replace('"', '\\"')
           .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{out}"'


def _val(value) -> str:
    """Valor TOML para los tipos del esquema."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, str):
        return _str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_val(item) for item in value) + "]"
    raise TypeError(f"tipo no soportado en la config: {type(value)!r}")


def dumps(config: dict) -> str:
    """Serializa la configuración completa (``config.toml``)."""
    lines = [f"version = {int(config.get('version', SCHEMA_VERSION))}", ""]
    for source in config.get("sources") or []:
        lines.append("[[sources]]")
        for key in ("id", "name", "kind", "source_type", "location"):
            lines.append(f"{key} = {_val(source.get(key, ''))}")
        lines.append(f"refresh_min = {_val(int(source.get('refresh_min') or 0))}")
        lines.append(f"enabled = {_val(bool(source.get('enabled', True)))}")
        lines.append("")
    for target in config.get("targets") or []:
        lines.append("[[targets]]")
        for key in ("id", "name", "port", "bind", "starting_number",
                    "custom_m3u", "custom_epg", "epg_source"):
            value = target.get(key)
            if value is None:
                continue
            lines.append(f"{key} = {_val(value)}")
        for key in ("m3u_sources", "epg_sources", "rules", "disabled"):
            lines.append(f"{key} = {_val(list(target.get(key) or []))}")
        priority = target.get("quality_priority") or {}
        if priority:
            items = ", ".join(f"{_str(token)} = {int(peso)}"
                              for token, peso in priority.items())
            lines.append(f"quality_priority = {{ {items} }}")
        lines.append("")
        overrides = target.get("overrides") or {}
        if overrides:
            # tabla hija del último [[targets]]: una url por línea y
            # entrecomillada, que es lo que INI no sabía hacer
            lines.append("[targets.overrides]")
            for url, channel_id in overrides.items():
                lines.append(f"{_str(url)} = {_str(channel_id)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def dumps_daemon(daemon: dict) -> str:
    """Serializa ``daemon.toml`` (lo escribe la GUI)."""
    lines = [f"version = {int(daemon.get('version', SCHEMA_VERSION))}"]
    for key in ("host", "port", "mcp_port", "token"):
        lines.append(f"{key} = {_val(daemon.get(key, ''))}")
    for key in ("autostart", "stop_on_exit"):
        lines.append(f"{key} = {_val(bool(daemon.get(key, False)))}")
    return "\n".join(lines) + "\n"


# -- lectura -------------------------------------------------------------------

def _check_version(data: dict, path: Path) -> dict:
    version = int(data.get("version") or 1)
    if version > SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: esquema versión {version}, este weteve entiende hasta "
            f"{SCHEMA_VERSION}. Actualiza weteve o usa otro fichero.")
    data.setdefault("version", version)
    return data


def load(path: Path) -> dict:
    """Lee ``config.toml``. Fichero inexistente = configuración vacía."""
    if not path.exists():
        return {"version": SCHEMA_VERSION, "sources": [], "targets": []}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    _check_version(data, path)
    data.setdefault("sources", [])
    data.setdefault("targets", [])
    for target in data["targets"]:
        target.setdefault("overrides", {})
        target.setdefault("quality_priority", {})
    return data


def load_daemon(path: Path) -> dict:
    """Lee ``daemon.toml`` con los valores por defecto de fábrica."""
    defaults = {"version": SCHEMA_VERSION, "host": "127.0.0.1",
                "port": 34400, "mcp_port": 34400, "token": "",
                "autostart": False, "stop_on_exit": False}
    if not path.exists():
        return defaults
    with open(path, "rb") as f:
        data = tomllib.load(f)
    _check_version(data, path)
    return {**defaults, **data}


# -- guardado ------------------------------------------------------------------

def save(path: Path, config: dict) -> None:
    """Guarda ``config.toml`` de forma atómica (con ``.bak``)."""
    write_atomic(path, dumps(config))


def save_daemon(path: Path, daemon: dict) -> None:
    """Guarda ``daemon.toml`` de forma atómica (con ``.bak``)."""
    write_atomic(path, dumps_daemon(daemon))


# -- importación desde el INI viejo -------------------------------------------

def from_legacy(ini_path: Path) -> tuple[dict, dict]:
    """Convierte ``config.ini`` en ``(config, daemon)``.

    Reutiliza los lectores existentes (cartera, targets y ``[DAEMON]``): la
    migración no reinterpreta el formato, solo lo vuelca al esquema nuevo.
    """
    from src.application.services.daemon.config import load_daemon_config
    from src.infrastructure.client.config_store import parse
    from src.infrastructure.client.persistencia import load_config
    from src.infrastructure.client.sources_store import read_catalog

    catalog = read_catalog(parse(ini_path)) if ini_path.exists() else None
    sources = []
    for entry in (catalog.all() if catalog is not None else []):
        sources.append({
            "id": entry.id, "name": entry.name, "kind": entry.kind,
            "source_type": entry.source_type, "location": entry.location,
            "refresh_min": entry.refresh_min, "enabled": entry.enabled,
        })
    targets = []
    if ini_path.exists():
        for target_id, t in sorted(load_config(ini_path)["targets"].items()):
            epg_source = t.get("epg_source")
            targets.append({
                "id": str(target_id),
                "name": t["name"],
                "port": int(t["port"]),
                "bind": t["bind"],
                "starting_number": int(t["starting_number"]),
                "custom_m3u": t["custom_m3u"],
                "custom_epg": t["custom_epg"],
                "epg_source": -1 if epg_source is None else int(epg_source),
                "m3u_sources": [ref.id for ref in t["m3u_refs"]],
                "epg_sources": [ref.id for ref in t["epg_refs"]],
                "rules": [rule.serialize() for rule in t["rules"]],
                "disabled": list(t["disabled_channels"]),
                "overrides": dict(t["epg_overrides"]),
                "quality_priority": {},
            })
    cfg = {"version": SCHEMA_VERSION, "sources": sources, "targets": targets}

    legacy_daemon = load_daemon_config(ini_path)
    daemon = {
        "version": SCHEMA_VERSION,
        "host": legacy_daemon.control_host,
        "port": legacy_daemon.control_port,
        "mcp_port": legacy_daemon.mcp_port,
        "token": legacy_daemon.mcp_token,
        "autostart": legacy_daemon.autostart,
        "stop_on_exit": legacy_daemon.stop_on_exit,
    }
    return cfg, daemon
