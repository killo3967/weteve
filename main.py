"""weteve — gestor de listas IPTV independiente.

Aplicación extraída de IPTVViewer. Orquesta: fuente M3U + filtros (sintaxis
xTeVe) + asignación EPG + servidor web local (playlist.m3u / epg.xml).

Uso: python main.py
"""

import configparser
import logging
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QDialog

from src.application.services.list_manager.models import FilterRule
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "weteve.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


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
            "port": parser.getint(sec, "port", fallback=8080),
            "bind": parser.get(sec, "bind", fallback="127.0.0.1"),
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
        epg_source = tgt.get("epg_source")
        parser[f"listmanager.target.{idx}"] = {
            "name": tgt.get("name", f"Target {idx + 1}"),
            "custom_m3u": str(tgt.get("custom_m3u", "")),
            "custom_epg": str(tgt.get("custom_epg", "")),
            "epg_source": str(epg_source if epg_source is not None else -1),
            "rules": rules_str,
            "epg_overrides": overrides_str,
            "port": str(tgt.get("port", 8080)),
            "bind": str(tgt.get("bind", "127.0.0.1")),
        }

    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)
    logger.info("Configuración guardada en %s", path)


def main() -> int:
    setup_logging()
    logger.info("Iniciando weteve")

    app = QApplication(sys.argv)
    app.setApplicationName("weteve")

    config = load_config(CONFIG_FILE)
    targets = config["targets"]

    # weteve no tiene listas internas: sources vacío, el diálogo trabaja en
    # modo "custom" (M3U/EPG propio por URL o fichero).
    dialog = ListManagerDialog(None, config={})
    dialog.setWindowTitle("weteve — Gestor de listas")
    result = dialog.exec()

    if result == QDialog.DialogCode.Accepted:
        new_config = dialog.get_target_config()
        if not new_config["name"]:
            new_config["name"] = f"Target {len(targets) + 1}"
        # get_target_config() devuelve la config del target, con rules ya
        # serializables. Se persiste en config.ini para la próxima ejecución.
        logger.info("Target guardado: %s (puerto %s)", new_config["name"], new_config.get("port"))
        # TODO(weteve): editar/gestionar múltiples targets desde la UI.
        targets[0] = new_config
        save_config(CONFIG_FILE, {"targets": targets})

    logger.info("weteve finalizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
