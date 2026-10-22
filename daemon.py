#!/usr/bin/env python3
"""Punto de entrada del daemon weteve.

Comandos:
  daemon.py run            Arranca en primer plano (usado por Task Scheduler)
  daemon.py status         Consulta el daemon en marcha (exit 0 = vivo)
  daemon.py stop           Pide apagado vía la API de control
  daemon.py install-task   Registra la tarea de inicio de sesión (schtasks)

Arquitectura: TargetSupervisor posee los targets (pipeline + PlaylistServer
por target). ControlApiServer (puerto ``control_port``) expone lo
mínimo para la UI; McpHttpTransport (mismo puerto, ``/mcp``) expone las 24
herramientas MCP con CRUD completo. TargetStore comparte ``daemon.toml`` con
la GUI (que escribe a través de la API).
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from src.application.services.daemon.config import (
    CLIENT_FILE,
    load_daemon_config,
)
from src.application.services.daemon.control_api import ControlApiServer
from src.application.services.daemon.mcp_server import McpStreamableServer
from src.application.services.daemon.mcp_tools import build_tool_registry
from src.application.services.daemon.mcp_transport import McpHttpTransport
from src.application.services.daemon.service import TargetService
from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore
from src.infrastructure.client.daemon_client import DaemonClient
from src.infrastructure.client.single_instance import (
    DaemonLock,
    lock_path,
    pid_path,
    read_address,
    write_pid,
)
from src.infrastructure.client.sources_store import (
    load_catalog,
)
from src.infrastructure.web.playlist_server import url_base_for
from src.infrastructure.web.rest_api import RestApi

logger = logging.getLogger("weteve.daemon")

# Etiquetas cortas de nivel para el log del daemon: [info], [warn], [error]…
_LEVEL_TAGS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


def default_log_path() -> Path:
    """Ruta del log del daemon: <repo>/logs/weteve-daemon.log."""
    return Path(__file__).resolve().parent / "logs" / "weteve-daemon.log"


class _LevelTagFilter(logging.Filter):
    """Añade ``record.leveltag`` (``info``/``warn``/``error``…).

    Es un atributo extra, no toca ``levelname``: así los handlers que
    imprimen ``%(levelname)s`` y las aserciones de los tests siguen viendo
    ``INFO``/``WARNING``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.leveltag = _LEVEL_TAGS.get(record.levelno,
                                          record.levelname.lower())
        return True


def setup_logging(log_path: Path | None = None,
                  level: int = logging.INFO) -> None:
    """Configura logging dual: consola (stderr) + fichero rotado por tamaño.

    Formato ``fecha [nivel] logger: mensaje``, con el nivel entre corchetes
    y en minúsculas (``[info]``, ``[warn]``). Idempotente: no duplica
    handlers si se llama más de una vez.
    """
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # ya configurado
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(leveltag)s] %(name)s: %(message)s")
    tag_filter = _LevelTagFilter()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(tag_filter)
    root.addHandler(console)

    path = log_path or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    file_h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                 encoding="utf-8")
    file_h.setFormatter(fmt)
    file_h.addFilter(tag_filter)
    root.addHandler(file_h)

DEFAULT_CONFIG = Path("daemon.toml")
#: Fichero del cliente (INI) que dice dónde escuchar; lo escribe la GUI.
DEFAULT_CLIENT = Path(CLIENT_FILE)
#: Fichero viejo, solo como origen de `migrate-config`.
DEFAULT_INI = Path("config.ini")


def build_daemon(config_path: Path = DEFAULT_CONFIG,
                 client_path: Path = DEFAULT_CLIENT) -> dict:
    """Construye y arranca el daemon.

    Todo se sirve en **un solo puerto** (``weteve.ini``, ``control_port``):
    control, REST (``/api/v1``), MCP (``/mcp``) y las playlists del supervisor
    (``/weteve/{id}/playlist.m3u`` y ``/epg.xml``). Los datos (fuentes y
    targets) se leen de ``daemon.toml``.

    Devuelve {"supervisor", "store", "service", "control_api", "server",
    "shutdown", "running"}.
    """
    cfg = load_daemon_config(client_path)
    if cfg.control_host not in ("127.0.0.1", "localhost") and not cfg.mcp_token:
        # La API y el MCP no distinguen usuarios: expuestos a la red sin token
        # cualquiera con acceso al puerto puede reescribir la configuración.
        raise RuntimeError(
            "mcp_token obligatorio cuando control_host no es loopback "
            f"(control_host={cfg.control_host})")
    # Configs antiguas de fuente única → cartera, antes de leer los targets.
    # (El formato viejo se convierte con `daemon.py migrate-config`; aquí no
    # se escribe el fichero: el demonio solo guarda cuando alguien cambia algo.)
    store = TargetStore(config_path)
    store.load()

    supervisor = TargetSupervisor(
        catalog_fn=lambda: load_catalog(config_path),
    )
    running = {"flag": True}

    def shutdown() -> None:
        running["flag"] = False

    # La API de control ya no abre socket: el servidor único enruta sus rutas.
    control_api = ControlApiServer(
        supervisor, host=cfg.control_host, port=cfg.control_port,
        token=cfg.mcp_token, on_shutdown=shutdown, target_store=store,
    )

    mcp_core = McpStreamableServer(server_name="weteve")
    mcp_core.set_tool_registry(
        build_tool_registry(supervisor, store, on_shutdown=shutdown))
    service = TargetService(supervisor, store)

    if cfg.mcp_port and cfg.mcp_port != cfg.control_port:
        logger.warning("[DAEMON] mcp_port=%d se ignora: todo se sirve en "
                       "control_port=%d", cfg.mcp_port, cfg.control_port)

    server = McpHttpTransport(
        mcp_core, host=cfg.control_host, port=cfg.control_port,
        token=cfg.mcp_token,
        rest_api=RestApi(service, on_shutdown=shutdown),
        control_api=control_api,
        playlist=supervisor.playlist_route,
    )
    if not server.start():
        raise RuntimeError(
            f"puerto {cfg.control_port} ocupado ¿hay otro daemon?")
    # Se anuncia una dirección navegable: con 0.0.0.0 el reproductor no puede
    # ir a ninguna parte, así que se publica la IP de la ruta por defecto.
    base = url_base_for(cfg.control_host, server.port)
    supervisor.set_base_url(base, server.port)

    logger.info("daemon weteve listo en %s (/mcp, /api/v1, "
                "/m3u/weteve_<id>.m3u) targets=%d",
                base, len(store.list()))
    return {
        "supervisor": supervisor,
        "store": store,
        "service": service,
        "control_api": control_api,
        "server": server,
        "shutdown": shutdown,
        "running": running,
    }


def _hay_demonio(address: str | None, client_path: Path) -> bool:
    """True si responde el demonio del candado o el del cliente configurado.

    Con dirección en el candado se pregunta a esa (puede estar en otro puerto
    del configurado); sin ella, al cliente de ``weteve.ini``.
    """
    if address:
        host, _, puerto = address.rpartition(":")
        try:
            return DaemonClient(host=host or "127.0.0.1", port=int(puerto),
                                timeout=2.0, retries=1).ping()
        except (TypeError, ValueError):
            return False
    return _control_client(client_path=client_path).ping()


def _avisar_ya_arrancado(candado: Path, client_path: Path) -> int:
    """Ya hay un demonio: se dice dónde está y se sale sin tocar la config."""
    address = read_address(pid_path(candado.parent / "daemon.toml"))
    if _hay_demonio(address, client_path):
        mensaje = (f"ya está en marcha en {address}" if address
                   else "ya está en marcha (responde el puerto configurado)")
        logger.info("%s — candado %s", mensaje, candado)
        print(mensaje)
        return 0
    mensaje = ("hay otro proceso con el candado del demonio pero no responde "
               f"({address or 'sin dirección'}): no se arranca otro")
    logger.error("%s — candado %s", mensaje, candado)
    print(mensaje)
    return 2


def cmd_run(config_path: Path = DEFAULT_CONFIG,
            client_path: Path = DEFAULT_CLIENT) -> int:
    setup_logging()
    logger.info("arrancando daemon weteve (config=%s)", config_path)
    # Candado ANTES de leer la configuración: dos demonios a la vez se pisan
    # daemon.toml, y el puerto solo no lo impide (con otro control_port, entra).
    candado = DaemonLock(lock_path(config_path))
    if not candado.acquire():
        return _avisar_ya_arrancado(candado.path, client_path)
    try:
        try:
            d = build_daemon(config_path, client_path)
        except RuntimeError as e:
            logger.error("%s", e)
            return 2
        # La dirección queda anotada para que la GUI sepa a quién preguntar
        # aunque escuche en un puerto distinto del configurado.
        write_pid(pid_path(config_path),
                  d["server"].url_base.removeprefix("http://"))

        # Arranca los targets definidos en daemon.toml
        for tid, target in d["store"].list().items():
            try:
                d["supervisor"].start_target(
                    tid, target, epg_path=d["store"].get_epg_path(tid))
            except Exception:
                logger.exception("target %s no arrancó", tid)

        try:
            while d["running"]["flag"]:
                threading.Event().wait(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("apagando daemon…")
            d["supervisor"].stop_all()
            d["server"].stop()
        return 0
    finally:
        candado.release()


def _control_client(port: int | None = None,
                    client_path: Path = DEFAULT_CLIENT) -> DaemonClient:
    """Cliente de control con el host, puerto y token de ``weteve.ini``.

    Los comandos de la CLI leen el mismo fichero que la GUI: con un puerto no
    estándar, `daemon.py status` no puede mentir diciendo que no responde.
    """
    cfg = load_daemon_config(client_path)
    return DaemonClient(host=cfg.control_host,
                        port=port if port is not None else cfg.control_port,
                        token=cfg.mcp_token)


def cmd_status(port: int | None = None,
               client_path: Path = DEFAULT_CLIENT) -> int:
    client = _control_client(port, client_path)
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


def cmd_stop(port: int | None = None,
             client_path: Path = DEFAULT_CLIENT) -> int:
    client = _control_client(port, client_path)
    if not client.shutdown():
        print("daemon weteve: no responde (¿ya parado?)")
        return 1
    print("daemon weteve: apagado solicitado")
    return 0


def cmd_install_task(config_path: Path = DEFAULT_CONFIG) -> int:
    """Registra la tarea de Task Scheduler: al iniciar sesión, daemon.py run.

    Sin ``shell=True``: los argumentos van como lista. Componer una cadena de
    shell con comillas anidadas se rompe en cuanto una ruta lleva espacios o
    comillas, y ``schtasks`` acepta argv sin problema.
    """
    import subprocess

    python = Path(sys.executable)
    script = Path(__file__).resolve()
    task_name = "weteve-daemon"
    # /TR es un único argumento con el comando, entre comillas: schtasks lo
    # guarda tal cual y lo ejecuta sin pasar por un shell.
    comando = f'"{python}" "{script}" run'
    argv = ["schtasks", "/Create", "/F", "/TN", task_name, "/SC", "ONLOGON",
            "/RL", "LIMITED", "/TR", comando]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error registrando tarea: {result.stderr.strip()}")
        return 1
    print(f"tarea '{task_name}' registrada: {python} {script} run")
    return 0


def cmd_migrate_config(ini_path: Path = DEFAULT_INI,
                       check: bool = False) -> int:
    """Convierte ``config.ini`` en ``daemon.toml`` (datos) + ``weteve.ini``.

    Idempotente: se puede repetir sin miedo (la escritura es atómica y deja
    ``.bak``). El ``config.ini`` **no se borra**: se conserva hasta que el
    usuario compruebe que todo va.
    """
    from src.application.services.daemon.config import (
        write_client_from_legacy,
    )
    from src.infrastructure.client import config_toml

    datos = ini_path.with_name(config_toml.CONFIG_FILE)
    cliente = ini_path.with_name(config_toml.CLIENT_FILE)
    if check:
        if not datos.exists() or not cliente.exists():
            print(f"faltan {datos.name}/{cliente.name}: "
                  "ejecuta `daemon.py migrate-config`")
            return 1
        if config_toml.load(datos) != config_toml.from_legacy(ini_path):
            print(f"{datos} no coincide con {ini_path}")
            return 1
        esperado = load_daemon_config(ini_path)
        if load_daemon_config(cliente) != esperado:
            print(f"{cliente} no coincide con {ini_path} ([DAEMON])")
            return 1
        print(f"ok: {datos.name} y {cliente.name} coinciden con {ini_path.name}")
        return 0
    if not ini_path.exists():
        print(f"no hay {ini_path} que migrar")
        return 1
    cfg = config_toml.from_legacy(ini_path)
    config_toml.save(datos, cfg)
    write_client_from_legacy(ini_path, cliente)
    mapeos = sum(len(t.get("overrides") or {}) for t in cfg["targets"])
    print(f"migrado: {datos} ({len(cfg['sources'])} fuentes, "
          f"{len(cfg['targets'])} target(s), {mapeos} mapeos)")
    print(f"migrado: {cliente} (host, puerto y token)")
    print(f"{ini_path.name} se conserva: bórralo cuando compruebes que va")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "run"
    if command == "run":
        return cmd_run(DEFAULT_CONFIG, DEFAULT_CLIENT)
    if command == "status":
        return cmd_status()
    if command == "stop":
        return cmd_stop()
    if command == "install-task":
        return cmd_install_task()
    if command == "migrate-config":
        return cmd_migrate_config(check="--check" in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
