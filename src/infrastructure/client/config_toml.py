"""Configuración de weteve en TOML: esquema, lectura, escritura y adaptadores.

Reparto de ficheros, un escritor cada uno:

- ``daemon.toml``: lo escribe **el demonio** (cartera de fuentes, targets con
  sus reglas, canales desactivados, mapeos y prioridad de calidad).
- ``weteve.ini``: lo escribe **la GUI** (host, puerto, token y preferencias
  de arranque). Es INI porque son seis claves planas y así no hay que migrar
  el arranque: ver ``daemon/config.py``.

Ventajas frente al viejo ``config.ini`` único: tipos nativos (enteros y
booleanos no son cadenas), arrays para listas (reglas, desactivados, selección
de fuentes) y **claves entrecomilladas** para los mapeos url → channel-id, que
en INI era imposible porque configparser parte las opciones por ``=`` y ``:``.

TOML se lee con ``tomllib`` (stdlib); la escritura es propia para no añadir
dependencias: solo se emiten los tipos del esquema (cadena, entero, booleano y
arrays de cadenas, tablas y arrays de tablas).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
)
from src.infrastructure.client.config_store import write_atomic

#: Versión del esquema. Un fichero con versión mayor no se toca: se avisa.
SCHEMA_VERSION = 2

#: Datos del demonio (este codec) y arranque del cliente (INI, lo escribe la GUI).
CONFIG_FILE = "daemon.toml"
CLIENT_FILE = "weteve.ini"


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
    """Serializa ``daemon.toml`` completo."""
    lines = [f"version = {int(config.get('version', SCHEMA_VERSION))}", ""]
    for source in config.get("sources") or []:
        lines.append("[[sources]]")
        for key in ("id", "name", "kind", "source_type", "location"):
            lines.append(f"{key} = {_val(source.get(key, ''))}")
        lines.append(f"refresh_min = {_val(int(source.get('refresh_min') or 0))}")
        # solo si aporta algo: un fichero sin prioridades no gana ruido
        if int(source.get("priority") or 0):
            lines.append(f"priority = {_val(int(source.get('priority') or 0))}")
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
    """Lee ``daemon.toml``. Fichero inexistente = configuración vacía."""
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


# -- adaptadores: catálogo y targets ------------------------------------------

def catalog_from(config: dict) -> SourceCatalog:
    """``SourceCatalog`` a partir de los ``[[sources]]`` del fichero."""
    return SourceCatalog([
        SourceEntry(id=str(raw.get("id", "")), name=str(raw.get("name", "")),
                    kind=raw.get("kind", "m3u"),
                    source_type=raw.get("source_type", "file"),
                    location=str(raw.get("location", "")),
                    refresh_min=int(raw.get("refresh_min") or 0),
                    enabled=bool(raw.get("enabled", True)),
                    priority=int(raw.get("priority") or 0))
        for raw in config.get("sources") or [] if raw.get("id")
    ])


def catalog_to_entries(catalog: SourceCatalog) -> list[dict]:
    """``[[sources]]`` a partir de la cartera.

    ``priority`` solo aparece si no es 0: así el fichero no gana una clave por
    fuente que nunca se ha tocado, y ``load`` devuelve exactamente lo que hay
    escrito (la comparación de ``migrate-config --check`` depende de eso).
    """
    out = []
    for entry in catalog.all():
        raw = {"id": entry.id, "name": entry.name, "kind": entry.kind,
               "source_type": entry.source_type, "location": entry.location,
               "refresh_min": entry.refresh_min, "enabled": entry.enabled}
        if entry.priority:
            raw["priority"] = entry.priority
        out.append(raw)
    return out


def target_to_dict(target: PipelineTarget, target_id: str) -> dict:
    """Target del dominio → bloque ``[[targets]]``."""
    return {
        "id": str(target_id),
        "name": target.name,
        "port": int(target.port),
        "bind": str(getattr(target, "bind", "") or "127.0.0.1"),
        "starting_number": int(target.starting_number),
        "custom_m3u": "",
        "custom_epg": "",
        "epg_source": int(target.epg_source_index if
                          target.epg_source_index is not None else -1),
        "m3u_sources": [ref.id for ref in target.source_refs],
        "epg_sources": [ref.id for ref in target.epg_refs],
        "rules": [rule.serialize() for rule in target.rules],
        "disabled": list(target.disabled_channels),
        "overrides": dict(target.epg_overrides),
        "quality_priority": dict(target.quality_priority or {}),
    }


def target_from_dict(raw: dict, catalog: SourceCatalog) -> PipelineTarget:
    """Bloque ``[[targets]]`` → target del dominio (resuelve refs por id)."""
    def refs(key: str) -> tuple[SourceRef, ...]:
        out = []
        for source_id in raw.get(key) or []:
            entry = catalog.get(str(source_id))
            if entry is not None:
                out.append(SourceRef(entry.id, entry.name, entry.location,
                                     entry.priority))
        return tuple(out)

    rules = tuple(FilterRule.parse(text) for text in raw.get("rules") or []
                  if str(text).strip())
    epg_source = raw.get("epg_source", -1)
    return PipelineTarget(
        name=str(raw.get("name", "")),
        # sin cartera, la fuente sigue siendo el M3U suelto del target antiguo
        source_index=str(raw.get("custom_m3u") or raw.get("id", "")),
        rules=rules,
        epg_source_index=(int(epg_source) if int(epg_source) >= 0 else None),
        epg_overrides={str(url): str(cid)
                       for url, cid in (raw.get("overrides") or {}).items()},
        port=int(raw.get("port") or 8080),
        starting_number=int(raw.get("starting_number") or 1),
        source_refs=refs("m3u_sources"),
        epg_refs=refs("epg_sources"),
        disabled_channels=tuple(str(url) for url in raw.get("disabled") or []),
        # vacío = prioridades de fábrica (None), no una tabla con todo a cero
        quality_priority={str(k): int(v) for k, v in
                          (raw.get("quality_priority") or {}).items()} or None,
    )


# -- escritura de alto nivel ---------------------------------------------------

def save(path: Path, config: dict) -> None:
    """Guarda ``daemon.toml`` de forma atómica (con ``.bak``)."""
    write_atomic(path, dumps(config))


def update(path: Path, mutate) -> dict:
    """Lee, muta y escribe: una lectura y una escritura por guardado."""
    config = load(path)
    mutate(config)
    save(path, config)
    return config


def save_catalog(path: Path, catalog: SourceCatalog) -> None:
    """Reemplaza la cartera; los targets pierden refs a fuentes que ya no están."""
    def mutate(config: dict) -> None:
        config["sources"] = catalog_to_entries(catalog)
        known = {entry.id for entry in catalog.all()}
        for target in config["targets"]:
            for key in ("m3u_sources", "epg_sources"):
                target[key] = [sid for sid in target.get(key) or []
                               if str(sid) in known]

    update(path, mutate)


def save_target(path: Path, target_id: str, target: PipelineTarget) -> None:
    """Inserta o reemplaza un target por id."""
    entry = target_to_dict(target, target_id)

    def mutate(config: dict) -> None:
        for index, raw in enumerate(config["targets"]):
            if str(raw.get("id")) == str(target_id):
                config["targets"][index] = entry
                return
        config["targets"].append(entry)

    update(path, mutate)


def delete_target(path: Path, target_id: str) -> bool:
    """Elimina un target del fichero. False si no estaba."""
    existed = {"value": False}

    def mutate(config: dict) -> None:
        keep = [raw for raw in config["targets"]
                if str(raw.get("id")) != str(target_id)]
        existed["value"] = len(keep) != len(config["targets"])
        config["targets"] = keep

    update(path, mutate)
    return existed["value"]


# -- importación desde el INI viejo -------------------------------------------

def from_legacy(ini_path: Path) -> dict:
    """Convierte la parte de datos de ``config.ini`` en la config nueva.

    Reutiliza los lectores existentes (cartera y targets): la migración no
    reinterpreta el formato, solo lo vuelca al esquema nuevo. El cliente
    (``[DAEMON]``) lo migra ``daemon/config.py``.
    """
    from src.infrastructure.client.config_store import parse
    from src.infrastructure.client.persistencia import load_config_ini
    from src.infrastructure.client.sources_store import read_catalog

    catalog = read_catalog(parse(ini_path)) if ini_path.exists() else None
    sources = catalog_to_entries(catalog) if catalog is not None else []
    targets = []
    if ini_path.exists():
        for target_id, t in sorted(load_config_ini(ini_path)["targets"].items()):
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
                "rules": list(t["rules"]),
                "disabled": list(t["disabled_channels"]),
                "overrides": dict(t["epg_overrides"]),
                "quality_priority": {},
            })
    return {"version": SCHEMA_VERSION, "sources": sources, "targets": targets}
