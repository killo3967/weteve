"""weteve — gestor de listas IPTV independiente.

Aplicación extraída de IPTVViewer. Orquesta: fuente M3U + filtros (sintaxis
xTeVe) + asignación EPG + servidor web local (playlist.m3u / epg.xml).

Uso: python main.py
"""

import logging
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from src.application.services.daemon.config import CLIENT_FILE
from src.infrastructure.client.config_toml import CONFIG_FILE as DATA_FILE
from src.infrastructure.client.persistencia import load_config
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

BASE_DIR = Path(__file__).resolve().parent
#: Datos del demonio (fuentes y targets) y arranque del cliente.
CONFIG_FILE = BASE_DIR / DATA_FILE
CLIENT_PATH = BASE_DIR / CLIENT_FILE

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
    target_cfg["_client_path"] = CLIENT_PATH
    target_cfg.setdefault("target_id", "0")
    dialog = ListManagerDialog(None, config=target_cfg)
    dialog.setWindowTitle("weteve — Gestor de listas")
    dialog.exec()
    # Persistencia: la hace el diálogo contra la API del demonio (escritor
    # único). Aquí no se reescribe daemon.toml para no pisar lo que escribió el
    # demonio ni los cambios hechos por MCP mientras la ventana estaba abierta.
    logger.info("weteve finalizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
