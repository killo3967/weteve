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

from src.application.services.daemon.scheduler import RefreshScheduler, target_interval_min
from src.application.services.list_manager.epg_matcher import EpgMatcher
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

    def __init__(self, catalog_fn=None) -> None:
        self._lock = threading.RLock()
        self._pipeline = ListManagerPipeline()
        self._targets: dict[str, PipelineTarget] = {}
        self._epg_paths: dict[str, str | None] = {}
        self._servers: dict[str, PlaylistServer] = {}
        self._results: dict[str, object] = {}   # último PipelineResult por target
        self._state: dict[str, dict] = {}       # channel_count, port, error
        # cartera de fuentes: callable() -> SourceCatalog (inyectable p/ tests)
        self._catalog_fn = catalog_fn
        self._scheduler = RefreshScheduler(self._scheduled_reload)

    # -- helpers internos ----------------------------------------------------

    @staticmethod
    def _render(result, epg) -> tuple[str, str]:
        """Convierte un PipelineResult en (playlist, epg_xml) listos para servir."""
        playlist = build_m3u(result.channels)
        epg_xml = build_xmltv(result.channels, epg)
        return playlist, epg_xml

    def _ensure_source(self, target: PipelineTarget, epg_path: str | None) -> None:
        """Registra la(s) fuente(s) del target en el pipeline.

        Con cartera (source_refs) registra la unión multi-fuente; sin ella,
        el flujo antiguo de fuente única (custom_m3u/custom_epg).
        """
        if target.source_refs:
            self._pipeline.register_merged(target.source_index,
                                           target.source_refs, target.epg_refs)
        else:
            self._pipeline.register_source(target.source_index, target.source_index,
                                           epg_path)

    def _remember(self, target_id: str, result) -> None:
        self._results[target_id] = result
        st = self._state.setdefault(target_id, {})
        st["channel_count"] = result.filtered_count
        st["total_input"] = result.total_input
        st["unmatched_count"] = len(result.unmatched)
        st.pop("error", None)

    # -- refresco periódico ----------------------------------------------------

    def _schedule_refresh(self, target_id: str, target: PipelineTarget) -> None:
        """Programa la recarga del target según el refresco de sus listas.

        Con cartera: min(refrescos activos referenciados) con suelo de 5 min;
        sin cartera o sin refrescos, no se programa nada (estático).
        """
        interval = None
        if self._catalog_fn is not None:
            try:
                interval = target_interval_min(self._catalog_fn(), target)
            except Exception:  # noqa: BLE001 - cartera ilegible = estático
                logger.warning("Cartera ilegible: %s queda sin refresco",
                               target_id, exc_info=True)
        if interval:
            self._scheduler.schedule(target_id, interval)
        else:
            self._scheduler.cancel(target_id)

    def _scheduled_reload(self, target_id: str) -> None:
        """Ciclo del scheduler: reejecutar el pipeline del target."""
        self.reload_target(target_id)

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
            self._schedule_refresh(target_id, target)

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
            self._scheduler.cancel(target_id)
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
            self._schedule_refresh(target_id, tgt)

            server = self._servers.get(target_id)
            if server is not None and server.is_running:
                epg = self._pipeline.get_epg(tgt.source_index)
                server.set_content(*self._render(result, epg))
            return True

    def stop_all(self) -> None:
        with self._lock:
            self._scheduler.cancel_all()
            for tid in list(self._servers):
                self._stop_server(tid)

    def remove_target(self, target_id: str) -> bool:
        """Elimina la definición del supervisor, parando su servidor si corría."""
        with self._lock:
            self._scheduler.cancel(target_id)
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

    def list_tv_channels(self, target_id: str, limit: int = 50,
                         offset: int = 0, unmatched_only: bool = False
                         ) -> dict:
        """Canales del último pipeline (requiere reload/start/run_pipeline).

        Devuelve una página {'channels': [...], 'total', 'count', 'offset'}.
        Con ``unmatched_only`` solo los que aún no tienen EPG asignado
        (matched_epg_id es None ni override).
        """
        with self._lock:
            result = self._results.get(target_id)
            if result is None:
                return {"channels": [], "total": 0, "count": 0,
                        "offset": int(offset)}
            rows = [
                {
                    "name": c.name,
                    "url": c.url,
                    "group": c.group,
                    "tvg_id": c.matched_epg_id,
                }
                for c in result.channels
            ]
        if unmatched_only:
            rows = [r for r in rows if not r["tvg_id"]]
        total = len(rows)
        page = rows[int(offset):int(offset) + int(limit)]
        return {"channels": page, "total": total, "count": len(page),
                "offset": int(offset)}

    def list_epg_channels(self, target_id: str, limit: int = 200,
                          offset: int = 0) -> dict:
        """Canales del XMLTV del target: [{'id', 'display_name'}, ...].

        Requiere que el target tenga EPG asignado y cargado (reload/
        run_pipeline previo). Paginado para no volcar catálogos enormes.
        """
        epg = None
        with self._lock:
            target = self._targets.get(target_id)
            if target is not None:
                epg = self._pipeline.get_epg(target.source_index)
        if epg is None:
            return {"error": (f"target {target_id} sin EPG cargado: "
                               "ejecuta reload_target o run_pipeline")}
        items = sorted(epg.channel_names.items())
        total = len(items)
        page = items[int(offset):int(offset) + int(limit)]
        return {"channels": [{"id": cid, "display_name": name}
                             for cid, name in page],
                "total": total, "count": len(page), "offset": int(offset)}

    def suggest_epg_matches(self, target_id: str, channel_name: str,
                            limit: int = 5) -> dict:
        """Candidatos EPG para un canal M3U por similitud de nombre.

        Usa EpgMatcher.suggestions() (normalización + difflib). Solo lectura:
        no asigna nada; la decisión queda para modify_mapping(s).
        """
        epg = None
        with self._lock:
            target = self._targets.get(target_id)
            if target is not None:
                epg = self._pipeline.get_epg(target.source_index)
        if epg is None:
            return {"error": (f"target {target_id} sin EPG cargado: "
                              "ejecuta reload_target o run_pipeline")}
        matcher = EpgMatcher(epg)
        suggestions = matcher.suggestions(channel_name, limit=int(limit))
        return {"suggestions": [{"id": cid, "display_name": name}
                                for cid, name in suggestions],
                "channel_name": channel_name}

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
