#!/usr/bin/env python3
"""Punto de entrada del daemon weteve.

Comandos:
  daemon.py run            Arranca en primer plano (usado por Task Scheduler)
  daemon.py status         Consulta el daemon en marcha (exit 0 = vivo)
  daemon.py stop           Pide apagado vía la API de control
  daemon.py install-task   Registra la tarea de inicio de sesión (schtasks)

Arquitectura: TargetSupervisor posee los targets (pipeline + PlaylistServer
por target). ControlApiServer (puerto [DAEMON] control_port) expone lo
mínimo para la UI; McpHttpTransport (mcp_port) expone las 12 herramientas
MCP con CRUD completo. TargetStore comparte config.ini con la UI.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from src.application.services.daemon.config import (
    DaemonConfig,
    load_daemon_config,
)
from src.application.services.daemon.control_api import ControlApiServer
from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore
from src.infrastructure.client.daemon_client import DaemonClient

logger = logging.getLogger("weteve.daemon")


def default_log_path() -> Path:
    """Ruta del log del daemon: <repo>/logs/weteve-daemon.log."""
    return Path(__file__).resolve().parent / "logs" / "weteve-daemon.log"


def setup_logging(log_path: Path | None = None) -> None:
    """Configura logging dual: consola (stderr) + fichero rotado por tamaño.

    Idempotente: no duplica handlers si se llama más de una vez.
    """
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # ya configurado
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    path = log_path or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    file_h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                 encoding="utf-8")
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

DEFAULT_CONFIG = Path("config.ini")

def build_daemon(config_path: Path = DEFAULT_CONFIG) -> dict:
    """Construye y arranca todos los componentes del daemon.

    Devuelve {"supervisor", "store", "control_api", "mcp_transport",
    "shutdown", "running"}.
    """
    cfg = load_daemon_config(config_path)
    store = TargetStore(config_path)
    store.load()

    supervisor = TargetSupervisor()
    running = {"flag": True}

    def shutdown() -> None:
        running["flag"] = False

    control_api = ControlApiServer(
        supervisor, host=cfg.control_host, port=cfg.control_port,
        token=cfg.mcp_token, on_shutdown=shutdown, target_store=store,
    )
    if not control_api.start():
        raise RuntimeError(
            f"puerto de control {cfg.control_port} ocupado ¿hay otro daemon?")

    mcp_core = McpStreamableServer(server_name="weteve")
    mcp_core.set_tool_registry(
        build_tool_registry(supervisor, store, on_shutdown=shutdown))
    mcp_transport = McpHttpTransport(
        mcp_core, host=cfg.control_host, port=cfg.mcp_port, token=cfg.mcp_token)
    if not mcp_transport.start():
        control_api.stop()
        raise RuntimeError(
            f"puerto MCP {cfg.mcp_port} ocupado ¿hay otro daemon?")

    logger.info("daemon weteve listo: control=%s mcp=%s/mcp targets=%d",
                control_api.url_base, mcp_transport.url_base, len(store.list()))
    return {
        "supervisor": supervisor,
        "store": store,
        "control_api": control_api,
        "mcp_transport": mcp_transport,
        "shutdown": shutdown,
        "running": running,
    }


def cmd_run(config_path: Path) -> int:
    setup_logging()
    try:
        d = build_daemon(config_path)
    except RuntimeError as e:
        logger.error("%s", e)
        return 2

    # Arranca los targets definidos en config.ini
    for tid, target in d["store"].list().items():
        try:
            d["supervisor"].start_target(
                tid, target, epg_path=d["store"].get_epg_path(tid))
        except Exception:  # noqa: BLE001
            logger.exception("target %s no arrancó", tid)

    try:
        while d["running"]["flag"]:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("apagando daemon…")
        d["supervisor"].stop_all()
        d["control_api"].stop()
        d["mcp_transport"].stop()
    return 0


def cmd_status(port: int = 34400) -> int:
    client = DaemonClient(port=port)
    status = client.status()
    if status is None:
        print("daemon weteve: no responde")
        return 1
    targets = status.get("targets", {})
    print(f"daemon weteve: activo, {len(targets)} target(s)")
    for tid, t in sorted(targets.items()):
        state = "sirviendo" if t.get("running") else "parado"
        extra = f", {t.get('channel_count', 0)} canales" if t.get("running") else ""
        err = f", ERROR: {t['error']}" if t.get("error") else ""
        print(f"  [{tid}] {t.get('name', '?')}: {state}{extra}{err}")
    return 0


def cmd_stop(port: int = 34400) -> int:
    client = DaemonClient(port=port)
    if not client.shutdown():
        print("daemon weteve: no responde (¿ya parado?)")
        return 1
    print("daemon weteve: apagado solicitado")
    return 0


def cmd_install_task(config_path: Path = DEFAULT_CONFIG) -> int:
    """Registra la tarea de Task Scheduler: al iniciar sesión, daemon.py run."""
    python = Path(sys.executable)
    script = Path(__file__).resolve()
    task_name = "weteve-daemon"
    cmd = (f'schtasks /Create /F /TN "{task_name}" /SC ONLOGON /RL LIMITED '
           f'/TR "\'{python}\' \'{script}\' run"')
    import subprocess
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error registrando tarea: {result.stderr.strip()}")
        return 1
    print(f"tarea '{task_name}' registrada: {python} {script} run")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "run"
    if command == "run":
        return cmd_run(DEFAULT_CONFIG)
    if command == "status":
        return cmd_status()
    if command == "stop":
        return cmd_stop()
    if command == "install-task":
        return cmd_install_task()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
