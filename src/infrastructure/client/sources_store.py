"""Persistencia de la cartera global de fuentes en config.ini.

Una sección ``[source.<id>]`` por entrada, con claves explícitas. Se usan
secciones (y no ``<id> = a|b|c``) porque los nombres de lista reales llevan
``|`` ("Series | HBO | 4K") y las rutas llevan espacios y acentos.

Cada target selecciona sus filas con ``m3u_sources`` / ``epg_sources`` (ids
separados por coma) dentro de su sección ``listmanager.target.N``.

Compatibilidad: un config.ini antiguo guarda una sola fuente por target en
``custom_m3u`` / ``custom_epg``. migrate_legacy_sources() convierte esas
ubicaciones en entradas de la cartera y enlaza los targets a ellas, de modo
que una config existente sigue funcionando sin tocarla el usuario.
"""

import configparser
import logging
from pathlib import Path

from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
    new_source_id,
)
from src.application.services.list_manager.models import SourceRef

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "source."
_TARGET_PREFIX = "listmanager.target."
_TRUE = ("1", "true", "yes", "on")
_VALID_KIND = ("m3u", "epg")
_VALID_TYPE = ("file", "url")


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
        kind=kind,
        source_type=source_type,
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


def selection_ids(refs, catalog: SourceCatalog) -> str:
    """Serializa refs como ids separados por coma, validando contra la cartera."""
    ids = []
    for ref in refs:
        if catalog.get(ref.id) is not None:
            ids.append(ref.id)
        else:
            logger.warning("Ref %s (%s) sin fila en la cartera: no se persiste",
                           ref.id, ref.name)
    return ",".join(ids)


def load_catalog(path: Path) -> SourceCatalog:
    """Lee la cartera desde ``[source.*]``. Sin sección, cartera vacía."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    entries = []
    for sec in parser.sections():
        if not sec.startswith(_SOURCE_PREFIX):
            continue
        entry = _read_entry(parser, sec, sec[len(_SOURCE_PREFIX):])
        if entry is not None:
            entries.append(entry)
    return SourceCatalog(entries)


def save_catalog(path: Path, catalog: SourceCatalog) -> None:
    """Escribe la cartera, reemplazando por completo las secciones previas.

    Las demás secciones (SETTINGS, [DAEMON], listmanager.target.*) se
    conservan intactas: se lee el fichero, se quitan solo ``source.*`` y se
    vuelven a escribir las entradas actuales en orden de tabla.
    """
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    for sec in [s for s in parser.sections() if s.startswith(_SOURCE_PREFIX)]:
        parser.remove_section(sec)
    _write_sections(parser, catalog)
    _dump(parser, path)


def load_target_selections(path: Path) -> dict[str, dict[str, list[str]]]:
    """Ids seleccionados por target: ``{target_id: {"m3u": [...], "epg": [...]}}``."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    out: dict[str, dict[str, list[str]]] = {}
    for sec in parser.sections():
        if not sec.startswith(_TARGET_PREFIX):
            continue
        tid = sec[len(_TARGET_PREFIX):]
        out[tid] = {
            "m3u": _split_ids(parser.get(sec, "m3u_sources", fallback="")),
            "epg": _split_ids(parser.get(sec, "epg_sources", fallback="")),
        }
    return out


def _split_ids(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _write_sections(parser: configparser.ConfigParser, catalog: SourceCatalog) -> None:
    """Sincroniza las secciones ``source.*`` del parser con la cartera."""
    for sec in [s for s in parser.sections() if s.startswith(_SOURCE_PREFIX)]:
        parser.remove_section(sec)
    for entry in catalog.all():
        sec = f"{_SOURCE_PREFIX}{entry.id}"
        parser.add_section(sec)
        parser.set(sec, "name", entry.name)
        parser.set(sec, "kind", entry.kind)
        parser.set(sec, "source_type", entry.source_type)
        parser.set(sec, "location", entry.location)
        parser.set(sec, "refresh_min", str(entry.refresh_min))
        parser.set(sec, "enabled", "1" if entry.enabled else "0")


def _dump(parser: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh)


def _ensure_source(catalog: SourceCatalog, kind: str, location: str) -> str:
    """Devuelve el id de la entrada para ``location``, creándola si no existe.

    La deduplicación es por ubicación (no por nombre): dos targets que usan la
    misma lista comparten una única fila de la cartera.
    """
    existing = next((e for e in catalog.all()
                     if e.kind == kind and e.location == location), None)
    if existing is not None:
        return existing.id
    source_type = "url" if _looks_like_url(location) else "file"
    entry = catalog.add_new(name=_name_from_location(location), kind=kind,
                            source_type=source_type, location=location,
                            refresh_min=0)
    return entry.id


def migrate_legacy_sources(path: Path) -> SourceCatalog:
    """Convierte ``custom_m3u``/``custom_epg`` de targets antiguos en cartera.

    Idempotente: si un target ya tiene ``m3u_sources``, no se toca. Al final
    reescribe el fichero una sola vez. Devuelve la cartera resultante.
    """
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")

    catalog = SourceCatalog(
        [e for e in (
            _read_entry(parser, sec, sec[len(_SOURCE_PREFIX):])
            for sec in parser.sections() if sec.startswith(_SOURCE_PREFIX)
        ) if e is not None]
    )

    changed = False
    for sec in parser.sections():
        if not sec.startswith(_TARGET_PREFIX):
            continue
        for field, kind in (("custom_m3u", "m3u"), ("custom_epg", "epg")):
            key = f"{kind}_sources"
            if parser.get(sec, key, fallback="").strip():
                continue  # ya migrado o elegido por el usuario
            location = parser.get(sec, field, fallback="").strip()
            if not location:
                continue
            sid = _ensure_source(catalog, kind, location)
            parser.set(sec, key, sid)
            changed = True
            logger.info("Migrada fuente %s de target %s a la cartera (%s)",
                        field, sec, sid)

    if changed:
        _write_sections(parser, catalog)
        _dump(parser, path)
    return catalog
