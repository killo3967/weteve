"""Serialización única de ``config.ini``.

Un solo módulo escribe el fichero: secciones de target
(``listmanager.target.N``), cartera de fuentes (``source.<id>``) y
``[DAEMON]``. Antes cada consumidor tenía su propia copia del formato
(``persistencia.save_config``, ``TargetStore.save``,
``sources_store.save_catalog`` y ``daemon.config.save_daemon_config``) y las
cuatro podían divergir: el formato del fichero solo debe cambiar aquí.

Todas las escrituras pasan por ``update()``: una lectura, las mutaciones y una
escritura, para que un guardado en lote no reescriba el fichero N veces.
"""

import configparser
import logging
from collections.abc import Callable
from pathlib import Path

from src.application.services.list_manager.models import FilterRule
from src.application.services.list_manager.source_catalog import SourceCatalog

logger = logging.getLogger(__name__)

SOURCE_PREFIX = "source."
TARGET_PREFIX = "listmanager.target."
DAEMON_SECTION = "DAEMON"


# -- lectura / escritura del fichero -------------------------------------------

def parse(path: Path) -> configparser.ConfigParser:
    """Parser con el contenido actual del fichero (vacío si no existe)."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    return parser


def dump(parser: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


def update(path: Path, mutate: Callable[[configparser.ConfigParser], None]) -> None:
    """Lee, muta y escribe: una sola lectura y una sola escritura."""
    parser = parse(path)
    mutate(parser)
    dump(parser, path)


# -- piezas compartidas --------------------------------------------------------

def selection_ids(refs, catalog: SourceCatalog) -> str:
    """Serializa refs como ids separados por coma, validando contra la cartera."""
    ids = []
    for ref in refs or ():
        if catalog.get(ref.id) is not None:
            ids.append(ref.id)
        else:
            logger.warning("Ref %s (%s) sin fila en la cartera: no se persiste",
                           ref.id, getattr(ref, "name", ""))
    return ",".join(ids)


def _rules_str(rules) -> str:
    return "|".join(
        r.serialize() if isinstance(r, FilterRule) else str(r)
        for r in rules or ()
    )


# -- secciones -----------------------------------------------------------------

def write_target(
    parser: configparser.ConfigParser,
    target_id: str | int,
    *,
    name: str,
    m3u: str,
    epg_path: str | None = None,
    epg_source: int | None = None,
    rules=(),
    overrides: dict[str, str] | None = None,
    disabled=(),
    port: int = 8080,
    starting_number: int = 1,
    bind: str | None = "127.0.0.1",
    m3u_refs=(),
    epg_refs=(),
    catalog: SourceCatalog | None = None,
) -> None:
    """Escribe (o reemplaza) la sección de un target."""
    catalog = catalog if catalog is not None else SourceCatalog()
    parser[f"{TARGET_PREFIX}{target_id}"] = {
        "name": name,
        "custom_m3u": str(m3u or ""),
        "custom_epg": str(epg_path or ""),
        "epg_source": str(epg_source if epg_source is not None else -1),
        "rules": _rules_str(rules),
        "epg_overrides": ";;".join(
            f"{url}::{cid}" for url, cid in (overrides or {}).items()),
        "disabled_channels": "||".join(
            str(u) for u in (disabled or ()) if u),
        "port": str(port),
        "starting_number": str(int(starting_number)),
        "bind": str(bind or "127.0.0.1"),
        "m3u_sources": selection_ids(m3u_refs, catalog),
        "epg_sources": selection_ids(epg_refs, catalog),
    }


def write_sources(parser: configparser.ConfigParser,
                  catalog: SourceCatalog) -> None:
    """Reemplaza por completo las secciones ``source.*`` con la cartera dada."""
    for sec in [s for s in parser.sections() if s.startswith(SOURCE_PREFIX)]:
        parser.remove_section(sec)
    for entry in catalog.all():
        sec = f"{SOURCE_PREFIX}{entry.id}"
        parser.add_section(sec)
        parser.set(sec, "name", entry.name)
        parser.set(sec, "kind", entry.kind)
        parser.set(sec, "source_type", entry.source_type)
        parser.set(sec, "location", entry.location)
        parser.set(sec, "refresh_min", str(entry.refresh_min))
        parser.set(sec, "enabled", "1" if entry.enabled else "0")


def write_daemon(parser: configparser.ConfigParser, cfg) -> None:
    """Escribe ``[DAEMON]`` desde un DaemonConfig."""
    parser[DAEMON_SECTION] = {
        "control_host": cfg.control_host,
        "control_port": str(cfg.control_port),
        "mcp_port": str(cfg.mcp_port),
        "mcp_token": cfg.mcp_token,
        "autostart": "1" if getattr(cfg, "autostart", False) else "0",
        "stop_on_exit": "1" if getattr(cfg, "stop_on_exit", False) else "0",
    }
