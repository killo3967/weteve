"""Escritura atómica de configuración + lectores del INI legado.

Este módulo **ya no serializa los datos del demonio**: eso es
``config_toml`` (``daemon.toml``). Le quedan dos papeles:

- **Escritura atómica** (``write_atomic``, ``dump``, ``update``) con copia
  ``.bak``: la comparten ``config_toml`` y ``daemon/config.py``.
- **Formato INI legado** (``parse``, ``parse_overrides``, ``write_daemon``):
  ``parse``/``parse_overrides`` solo los lee ``python daemon.py
  migrate-config`` para convertir un ``config.ini``. ``write_daemon`` escribe
  ``[DAEMON]`` en ``weteve.ini``, el fichero de arranque que lee la GUI.

**Congelado el 2026-10-22** (tarea T02 de ``odd/tasks/auditoria-2026-10-21.md``):
no se añaden claves ni escritores nuevos aquí. El reparto vigente es
``daemon.toml`` (datos, lo escribe el demonio) + ``weteve.ini`` (arranque, lo
escribe la GUI); ver ``docs/diataxis/referencia-configuracion.md``. El INI viejo
se mantiene indefinidamente **solo** para migrar: ``config.ini`` no se escribe
ni se lee en el flujo normal.
"""

import configparser
import io
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Sección de ``weteve.ini`` (arranque del demonio). El resto de este módulo
#: solo lee el formato viejo.
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
    """Copia de la última versión buena: ``<nombre>.bak`` junto al original."""
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
            # Limpieza best-effort: la excepción original se relanza igualmente
            # (no enmascarar el fallo de escritura con el fallo del borrado).
            pass
        raise


def dump(parser: configparser.ConfigParser, path: Path) -> None:
    """Escribe el parser completo con la escritura atómica."""
    buffer = io.StringIO()
    parser.write(buffer)
    write_atomic(path, buffer.getvalue())


def update(path: Path, mutate: Callable[[configparser.ConfigParser], None]) -> None:
    """Lee, muta y escribe: una sola lectura y una sola escritura."""
    parser = parse(path)
    mutate(parser)
    dump(parser, path)


# -- lectura del formato viejo ------------------------------------------------

def parse_overrides(raw: str | None) -> dict[str, str]:
    """``url -> channel-id`` desde el valor de ``epg_overrides``.

    Acepta el formato actual (un par por línea) y el viejo (todo en una línea
    separado por ``;;``): un ``config.ini`` de antes se sigue leyendo igual.
    Solo lo usa ``migrate-config`` (capa legada congelada el 2026-10-22).
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


# -- escritura del arranque (weteve.ini) --------------------------------------

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
