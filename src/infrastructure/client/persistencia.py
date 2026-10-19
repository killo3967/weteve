"""Lectura de la configuración del demonio para weteve (``load_config``).

Devuelve el diccionario que consume la GUI (mismo perfil que daba el viejo
``config.ini``), pero leyendo ``daemon.toml`` a través del codec único.
"""

import logging
from pathlib import Path

from src.infrastructure.client import config_toml

logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    """Targets de ``daemon.toml`` en el perfil que espera la UI.

    Las reglas llegan ya parseadas (``FilterRule``) y las fuentes como
    ``SourceRef``: la UI no tiene que saber nada del formato del fichero.
    """
    config = config_toml.load(path)
    catalog = config_toml.catalog_from(config)
    targets: dict[int, dict] = {}
    for raw in config["targets"]:
        target = config_toml.target_from_dict(raw, catalog)
        try:
            target_id = int(str(raw.get("id", "0")))
        except ValueError:
            logger.warning("target con id no numérico en %s: %r", path,
                           raw.get("id"))
            continue
        targets[target_id] = {
            "name": target.name,
            # weteve es standalone: el modo de origen es siempre "custom"
            # (URL o fichero propio), no hay listas de una app anfitriona.
            "source_mode": "custom",
            "custom_m3u": str(raw.get("custom_m3u") or ""),
            "custom_epg": str(raw.get("custom_epg") or ""),
            "epg_source": target.epg_source_index,
            "rules": list(target.rules),
            "epg_overrides": dict(target.epg_overrides),
            "disabled_channels": list(target.disabled_channels),
            "quality_priority": dict(target.quality_priority or {}),
            "port": target.port,
            "starting_number": target.starting_number,
            "bind": str(raw.get("bind") or "127.0.0.1"),
            "m3u_refs": list(target.source_refs),
            "epg_refs": list(target.epg_refs),
        }
    return {"targets": targets}


def load_config_ini(path: Path) -> dict:
    """Lectura del ``config.ini`` **viejo** (solo para ``migrate-config``).

    El resto de la aplicación ya lee ``daemon.toml``; esto se queda para poder
    convertir un fichero antiguo sin reinterpretarlo a mano.
    """
    from src.infrastructure.client.config_store import parse, parse_overrides
    from src.infrastructure.client.sources_store import refs_for_target

    parser = parse(path)
    targets: dict[int, dict] = {}
    index = 0
    while parser.has_section(f"listmanager.target.{index}"):
        sec = f"listmanager.target.{index}"
        raw_rules = parser.get(sec, "rules", fallback="")
        raw_disabled = parser.get(sec, "disabled_channels", fallback="")
        saved_epg_src = parser.get(sec, "epg_source", fallback="-1")
        targets[index] = {
            "name": parser.get(sec, "name", fallback=f"Target {index + 1}"),
            "custom_m3u": parser.get(sec, "custom_m3u", fallback=""),
            "custom_epg": parser.get(sec, "custom_epg", fallback=""),
            "epg_source": (int(saved_epg_src) if saved_epg_src.isdigit()
                           else None),
            "rules": [rule.strip() for rule in raw_rules.split("|")
                      if rule.strip()],
            "epg_overrides": parse_overrides(
                parser.get(sec, "epg_overrides", fallback="")),
            "disabled_channels": [u.strip() for u in raw_disabled.split("||")
                                  if u.strip()],
            "port": parser.getint(sec, "port", fallback=8080),
            "starting_number": parser.getint(sec, "starting_number",
                                             fallback=1),
            "bind": parser.get(sec, "bind", fallback="127.0.0.1"),
            "m3u_refs": list(refs_for_target(parser, sec, "m3u")),
            "epg_refs": list(refs_for_target(parser, sec, "epg")),
        }
        index += 1
    return {"targets": targets}
