"""Lectura de config.ini para weteve (``load_config``).

La escritura vive en ``config_store`` y la usa el demonio: la UI ya no
reescribe el fichero (solo ``[DAEMON]``, que el demonio lee al arrancar).
"""

import logging
from pathlib import Path

from src.application.services.list_manager.models import FilterRule
from src.infrastructure.client.config_store import parse, parse_overrides
from src.infrastructure.client.sources_store import refs_for_target

logger = logging.getLogger(__name__)

def load_config(path: Path) -> dict:
    """Carga config.ini. Secciones: SETTINGS y listmanager.target.N (una por
    target, igual que en IPTVViewer). Devuelve el dict de targets."""
    # mismo parser que escribe (sin interpolación): una url con ``%20`` se lee
    parser = parse(path)

    targets: dict[int, dict] = {}
    j = 0
    while parser.has_section(f"listmanager.target.{j}"):
        sec = f"listmanager.target.{j}"
        raw_rules = parser.get(sec, "rules", fallback="")
        rules = [FilterRule.parse(r.strip()) for r in raw_rules.split("|") if r.strip()]
        epg_overrides = parse_overrides(
            parser.get(sec, "epg_overrides", fallback=""))
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
            "starting_number": parser.getint(sec, "starting_number",
                                             fallback=1),
            "bind": parser.get(sec, "bind", fallback="127.0.0.1"),
            "m3u_refs": list(refs_for_target(parser, sec, "m3u")),
            "epg_refs": list(refs_for_target(parser, sec, "epg")),
        }
        j += 1
    return {"targets": targets}




