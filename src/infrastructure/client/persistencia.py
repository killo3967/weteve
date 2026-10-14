"""Persistencia de config.ini para weteve (carga y guardado de targets)."""

import configparser
import logging
from pathlib import Path

from src.application.services.list_manager.models import FilterRule, SourceRef
from src.infrastructure.client.sources_store import refs_for_target, selection_ids, read_catalog

logger = logging.getLogger(__name__)

def load_config(path: Path) -> dict:
    """Carga config.ini. Secciones: SETTINGS y listmanager.target.N (una por
    target, igual que en IPTVViewer). Devuelve el dict de targets."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")

    targets: dict[int, dict] = {}
    j = 0
    while parser.has_section(f"listmanager.target.{j}"):
        sec = f"listmanager.target.{j}"
        raw_rules = parser.get(sec, "rules", fallback="")
        rules = [FilterRule.parse(r.strip()) for r in raw_rules.split("|") if r.strip()]
        raw_overrides = parser.get(sec, "epg_overrides", fallback="")
        epg_overrides = {}
        for pair in raw_overrides.split(";;"):
            if "::" in pair:
                url, cid = pair.split("::", 1)
                epg_overrides[url] = cid
        raw_disabled = parser.get(sec, "disabled_channels", fallback="")
        disabled_channels = tuple(
            u.strip() for u in raw_disabled.split("||") if u.strip()
        )
        saved_epg_src = parser.get(sec, "epg_source", fallback="-1")
        targets[j] = {
            "name": parser.get(sec, "name", fallback=f"Target {j+1}"),
            # weteve es standalone: el modo de origen es siempre "custom"
            # (URL o fichero propio), no hay listas de una app anfitriona.
            "source_mode": "custom",
            "custom_m3u": parser.get(sec, "custom_m3u", fallback=""),
            "custom_epg": parser.get(sec, "custom_epg", fallback=""),
            "epg_source": int(saved_epg_src) if saved_epg_src.isdigit() else None,
            "rules": rules,
            "epg_overrides": epg_overrides,
            "disabled_channels": list(disabled_channels),
            "port": parser.getint(sec, "port", fallback=8080),
            "bind": parser.get(sec, "bind", fallback="127.0.0.1"),
            "m3u_refs": list(refs_for_target(parser, sec, "m3u")),
            "epg_refs": list(refs_for_target(parser, sec, "epg")),
        }
        j += 1
    return {"targets": targets}


def save_config(path: Path, config: dict) -> None:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")

    for idx, tgt in config.get("targets", {}).items():
        rules = tgt.get("rules", [])
        rules_str = "|".join(
            r.serialize() if isinstance(r, FilterRule) else str(r) for r in rules
        )
        overrides = tgt.get("epg_overrides", {})
        overrides_str = ";;".join(f"{u}::{c}" for u, c in overrides.items())
        disabled_list = tgt.get("disabled_channels", []) or []
        disabled_str = "||".join(
            u for u in (str(x) for x in disabled_list) if u
        )
        epg_source = tgt.get("epg_source")
        catalog = read_catalog(parser)
        parser[f"listmanager.target.{idx}"] = {
            "name": tgt.get("name", f"Target {idx + 1}"),
            "custom_m3u": str(tgt.get("custom_m3u", "")),
            "custom_epg": str(tgt.get("custom_epg", "")),
            "epg_source": str(epg_source if epg_source is not None else -1),
            "rules": rules_str,
            "epg_overrides": overrides_str,
            "disabled_channels": disabled_str,
            "port": str(tgt.get("port", 8080)),
            "bind": str(tgt.get("bind", "127.0.0.1")),
            "m3u_sources": selection_ids(tgt.get("m3u_refs", []), catalog),
            "epg_sources": selection_ids(tgt.get("epg_refs", []), catalog),
        }

    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)
    logger.info("Configuración guardada en %s", path)


