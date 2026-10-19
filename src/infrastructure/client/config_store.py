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
import io
import logging
import os
import shutil
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
    """Parser con el contenido actual del fichero (vacío si no existe).

    Sin interpolación (``%``): las ubicaciones son URLs y un ``%20`` o un
    ``%C3%A9`` en una ruta no son un error de configuración.
    """
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        parser.read(path, encoding="utf-8")
    return parser


def backup_path(path: Path) -> Path:
    """Copia de la última versión buena: ``config.ini.bak``."""
    return path.with_name(path.name + ".bak")


def write_atomic(path: Path, text: str) -> None:
    """Escribe ``text`` de forma atómica y guarda la versión anterior.

    Se escribe en ``<nombre>.tmp``, se fuerza a disco y se reemplaza con
    ``os.replace`` (atómico en el mismo volumen). Antes de reemplazar se copia
    el fichero actual a ``<nombre>.bak``. Si algo falla a mitad, el original
    queda intacto y el temporal se borra.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            try:
                shutil.copy2(path, backup_path(path))
            except OSError:  # la copia es una red de seguridad, no un requisito
                logger.warning("no se pudo copiar %s a %s", path,
                               backup_path(path), exc_info=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def dump(parser: configparser.ConfigParser, path: Path) -> None:
    """Escribe el parser completo (config.ini) con la escritura atómica."""
    buffer = io.StringIO()
    parser.write(buffer)
    write_atomic(path, buffer.getvalue())


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


def parse_overrides(raw: str | None) -> dict[str, str]:
    """``url -> channel-id`` desde el valor de ``epg_overrides``.

    Acepta el formato actual (un par por línea) y el viejo (todo en una línea
    separado por ``;;``): un config.ini de antes se sigue leyendo igual.
    """
    pairs: dict[str, str] = {}
    for line in (raw or "").replace(";;", "\n").splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        url, cid = line.split("::", 1)
        url, cid = url.strip(), cid.strip()
        if url and cid:
            pairs[url] = cid
    return pairs


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
        # Un mapeo por línea (configparser escribe las líneas siguientes
        # indentadas): el fichero queda legible y cada cambio se ve en el diff.
        "epg_overrides": "".join(
            f"\n{url}::{cid}" for url, cid in (overrides or {}).items()),
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
