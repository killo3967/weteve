"""TargetSupervisor: único dueño del ciclo de vida de los targets servidos.

Por cada target registra un ``PipelineTarget`` (definición) y gestiona su
``PlaylistServer`` + el último resultado del pipeline. Los servidores HTTP
corren en hilos daemon; el acceso desde MCP / API de control llega por hilos
distintos, así que todos los métodos públicos van protegidos con un lock.

Registro de fuentes: ``PipelineTarget.source_index`` es la ruta/URL M3U (modo
standalone "custom"). El EPG XMLTV asociado se pasa aparte (``epg_path``) y se
registra en el pipeline junto a la fuente, igual que hace el diálogo Qt.
"""

import logging
import threading

from src.application.services.list_manager.models import PipelineTarget
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.application.services.list_manager.playlist_builder import (
    build_m3u,
    build_xmltv,
)
from src.infrastructure.web.playlist_server import PlaylistServer

logger = logging.getLogger(__name__)

class TargetSupervisor:
    """Gestiona N targets: pipeline cacheado + servidor HTTP por target."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pipeline = ListManagerPipeline()
        self._targets: dict[str, PipelineTarget] = {}
        self._epg_paths: dict[str, str | None] = {}
        self._servers: dict[str, PlaylistServer] = {}
        self._results: dict[str, object] = {}   # último PipelineResult por target
        self._state: dict[str, dict] = {}       # channel_count, port, error

    # -- helpers internos ----------------------------------------------------

    @staticmethod
    def _render(result, epg) -> tuple[str, str]:
        """Convierte un PipelineResult en (playlist, epg_xml) listos para servir."""
        playlist = build_m3u(result.channels)
        epg_xml = build_xmltv(result.channels, epg) if epg is not None else ""
        return playlist, epg_xml

    def _ensure_source(self, target: PipelineTarget, epg_path: str | None) -> None:
        """Registra (o actualiza) la fuente M3U+EPG del target en el pipeline."""
        self._pipeline.register_source(target.source_index, target.source_index, epg_path)

    def _remember(self, target_id: str, result) -> None:
        self._results[target_id] = result
        st = self._state.setdefault(target_id, {})
        st["channel_count"] = result.filtered_count
        st["total_input"] = result.total_input
        st["unmatched_count"] = len(result.unmatched)
        st.pop("error", None)

    # -- ciclo de vida -------------------------------------------------------

    def start_target(
        self, target_id: str, target: PipelineTarget, epg_path: str | None = None
    ) -> bool:
        """Construye el pipeline del target y arranca su servidor HTTP."""
        with self._lock:
            self._targets[target_id] = target
            self._epg_paths[target_id] = epg_path
            try:
                self._ensure_source(target, epg_path)
                result = self._pipeline.run(target)
            except Exception as e:
                logger.error("Pipeline falló para %s: %s", target_id, e)
                self._state[target_id] = {"error": str(e)}
                self._stop_server(target_id)
                return False
            self._remember(target_id, result)

            server = self._servers.get(target_id)
            if server is not None and server.is_running and server.port != target.port:
                # el puerto cambió: rebind
                server.stop()
                server = None
            if server is None:
                server = PlaylistServer(host="127.0.0.1", port=target.port or 0)
                self._servers[target_id] = server

            epg = self._pipeline.get_epg(target.source_index)
            server.set_content(*self._render(result, epg))
            if not server.start():
                self._state[target_id]["error"] = (
                    f"Puerto {server.port} ocupado o no disponible"
                )
                return False
            self._state[target_id]["port"] = server.port
            return True

    def stop_target(self, target_id: str) -> bool:
        """Para el servidor del target; False si no estaba arrancado."""
        with self._lock:
            return self._stop_server(target_id)

    def _stop_server(self, target_id: str) -> bool:
        server = self._servers.pop(target_id, None)
        if server is None:
            return False
        server.stop()
        st = self._state.setdefault(target_id, {})
        st.pop("port", None)
        return True

    def reload_target(
        self, target_id: str, target: PipelineTarget | None = None,
        epg_path: str | None = None,
    ) -> bool:
        """Reejecuta el pipeline; si el target estaba sirviendo, actualiza el contenido.

        Con ``target`` None reutiliza la definición registrada. Un reload sobre
        un target no arrancado construye la cache pero no abre puerto.
        """
        with self._lock:
            tgt = target or self._targets.get(target_id)
            if tgt is None:
                return False
            self._targets[target_id] = tgt
            if epg_path is not None or target is not None:
                self._epg_paths[target_id] = epg_path or self._epg_paths.get(target_id)
            try:
                self._ensure_source(tgt, self._epg_paths.get(target_id))
                result = self._pipeline.run(tgt)
            except Exception as e:
                logger.error("Pipeline falló al recargar %s: %s", target_id, e)
                self._state[target_id] = {"error": str(e)}
                return False
            self._remember(target_id, result)

            server = self._servers.get(target_id)
            if server is not None and server.is_running:
                epg = self._pipeline.get_epg(tgt.source_index)
                server.set_content(*self._render(result, epg))
            return True

    def stop_all(self) -> None:
        with self._lock:
            for tid in list(self._servers):
                self._stop_server(tid)

    def remove_target(self, target_id: str) -> bool:
        """Elimina la definición del supervisor, parando su servidor si corría."""
        with self._lock:
            self._stop_server(target_id)
            existed = self._targets.pop(target_id, None) is not None
            self._results.pop(target_id, None)
            self._state.pop(target_id, None)
            self._epg_paths.pop(target_id, None)
            return existed

    # -- consultas -------------------------------------------------------------

    def get_status(self) -> dict:
        """Estado agregado: {"targets": {id: {...}}} con running/URLs/errores."""
        with self._lock:
            out: dict[str, dict] = {}
            for tid, tgt in self._targets.items():
                server = self._servers.get(tid)
                st = dict(self._state.get(tid, {}))
                running = server is not None and server.is_running
                out[tid] = {
                    "name": tgt.name,
                    "running": running,
                    "port": st.get("port") or (server.port if server else None),
                    "channel_count": st.get("channel_count"),
                    "error": st.get("error"),
                    "playlist_url": (
                        f"http://{server.host}:{server.port}/playlist.m3u"
                        if running else None
                    ),
                    "epg_url": (
                        f"http://{server.host}:{server.port}/epg.xml"
                        if running else None
                    ),
                }
            return {"targets": out}

    def preview_channels(self, target_id: str, limit: int = 20) -> list[dict]:
        """Primeros ``limit`` canales del último pipeline (requiere reload/start)."""
        with self._lock:
            result = self._results.get(target_id)
            if result is None:
                return []
            return [
                {
                    "name": c.name,
                    "url": c.url,
                    "group": c.group,
                    "tvg_id": c.matched_epg_id,
                }
                for c in result.channels[:limit]
            ]

    def run_pipeline(self, target_id: str) -> dict:
        """Ejecuta el pipeline del target y devuelve métricas."""
        with self._lock:
            tgt = self._targets.get(target_id)
            if tgt is None:
                return {"error": f"target {target_id} no existe"}
            try:
                result = self._pipeline.run(tgt)
            except Exception as e:
                return {"error": str(e)}
            self._remember(target_id, result)
            return {
                "total_input": result.total_input,
                "filtered_count": result.filtered_count,
                "unmatched_count": len(result.unmatched),
            }
