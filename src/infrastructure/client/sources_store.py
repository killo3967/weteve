"""Cartera de fuentes: lectura/escritura en ``daemon.toml`` y lector legado.

La cartera vive en los bloques ``[[sources]]`` del TOML (ver ``config_toml``).
Aquí quedan:
- ``load_catalog`` / ``save_catalog``: el acceso que usan demonio y GUI.
- ``read_catalog`` / ``refs_for_target``: **lectores del INI viejo**, usados
  solo por ``daemon.py migrate-config`` para convertir un ``config.ini``.
"""

import configparser
import logging
from pathlib import Path
from typing import cast

from src.application.services.list_manager.models import SourceRef
from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
    SourceKind,
    SourceType,
)

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "source."
_TARGET_PREFIX = "listmanager.target."
_TRUE = ("1", "true", "yes", "on")
_VALID_KIND = ("m3u", "epg")
_VALID_TYPE = ("file", "url", "movistar")


def _parse_bool(raw: str, default: bool = True) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE


def _name_from_location(location: str) -> str:
    """Nombre legible derivado de la ruta o URL (sin extensión)."""
    tail = location.replace("\\", "/").rstrip("/").split("/")[-1]
    tail = tail.split("?")[0].split("#")[0]
    if "." in tail:
        tail = tail.rsplit(".", 1)[0]
    return tail or location


def _looks_like_url(location: str) -> bool:
    low = location.strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def _read_entry(parser: configparser.ConfigParser, sec: str, sid: str) -> SourceEntry | None:
    kind = parser.get(sec, "kind", fallback="").strip().lower()
    source_type = parser.get(sec, "source_type", fallback="").strip().lower()
    location = parser.get(sec, "location", fallback="").strip()
    if kind not in _VALID_KIND or source_type not in _VALID_TYPE or not location:
        logger.warning("Fuente %s malformada (kind=%r type=%r location=%r): se ignora",
                       sid, kind, source_type, location)
        return None
    raw_refresh = parser.get(sec, "refresh_min", fallback="0").strip()
    try:
        refresh = max(0, int(raw_refresh))
    except ValueError:
        logger.warning("refresh_min no numérico en fuente %s (%r): se toma 0", sid, raw_refresh)
        refresh = 0
    return SourceEntry(
        id=sid,
        name=parser.get(sec, "name", fallback="").strip() or _name_from_location(location),
        kind=cast(SourceKind, kind),
        source_type=cast(SourceType, source_type),
        location=location,
        refresh_min=refresh,
        enabled=_parse_bool(parser.get(sec, "enabled", fallback="1")),
    )


def read_catalog(parser: configparser.ConfigParser) -> SourceCatalog:
    """Lee la cartera desde un parser ya cargado (sin I/O)."""
    entries = []
    for sec in parser.sections():
        if not sec.startswith(_SOURCE_PREFIX):
            continue
        entry = _read_entry(parser, sec, sec[len(_SOURCE_PREFIX):])
        if entry is not None:
            entries.append(entry)
    return SourceCatalog(entries)


def refs_for_target(parser: configparser.ConfigParser, sec: str,
                    kind: str) -> tuple[SourceRef, ...]:
    """Selección ``<kind>_sources`` de una sección target como SourceRefs.

    ``sec`` es la sección completa (p. ej. ``listmanager.target.0``).
    Resuelve cada id contra ``[source.*]``; los huérfanos se descartan.
    """
    raw = parser.get(sec, f"{kind}_sources", fallback="")
    catalog = read_catalog(parser)
    refs = []
    for sid in (p.strip() for p in raw.split(",") if p.strip()):
        entry = catalog.get(sid)
        if entry is not None:
            refs.append(SourceRef(entry.id, entry.name, entry.location))
        else:
            logger.warning("%s referencia la fuente inexistente %r: se ignora",
                           sec, sid)
    return tuple(refs)




def load_catalog(path: Path) -> SourceCatalog:
    """Lee la cartera de fuentes de ``daemon.toml``."""
    from src.infrastructure.client import config_toml

    return config_toml.catalog_from(config_toml.load(path))


def save_catalog(path: Path, catalog: SourceCatalog) -> None:
    """Reemplaza la cartera en ``daemon.toml`` (una lectura, una escritura).

    Las refs de los targets a fuentes que ya no están se podan: si no, el
    target apuntaría a ids fantasma y el pipeline no encontraría la lista.
    """
    from src.infrastructure.client import config_toml

    config_toml.save_catalog(path, catalog)
