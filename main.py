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

from src.infrastructure.client.persistencia import load_config, save_config

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


def main() -> int:
    setup_logging()
    logger.info("Iniciando weteve")

    app = QApplication(sys.argv)
    app.setApplicationName("weteve")

    config = load_config(CONFIG_FILE)
    targets = config["targets"]

    # weteve no tiene listas internas: sources vacío, el diálogo trabaja en
    # modo "custom" (M3U/EPG propio por URL o fichero). El target 0 es el
    # que edita el diálogo; el daemon lo sirve permanentemente.
    target_cfg = dict(targets.get("0", targets.get(0, {})))
    target_cfg["_config_path"] = CONFIG_FILE
    target_cfg.setdefault("target_id", "0")
    dialog = ListManagerDialog(None, config=target_cfg)
    dialog.setWindowTitle("weteve — Gestor de listas")
    result = dialog.exec()

    if result == QDialog.DialogCode.Accepted:
        new_config = dialog.get_target_config()
        if not new_config["name"]:
            new_config["name"] = f"Target {len(targets) + 1}"
        # get_target_config() devuelve la config del target, con rules ya
        # serializables. Se persiste en config.ini para la próxima ejecución.
        logger.info("Target guardado: %s (puerto %s)", new_config["name"], new_config.get("port"))
        # El panel «Demonio» ya persiste vía TargetStore; aquí solo se
        # mantiene el formato legacy por compatibilidad.
        targets[0] = new_config
        save_config(CONFIG_FILE, {"targets": targets})

    logger.info("weteve finalizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
