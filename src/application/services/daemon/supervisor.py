"""TargetSupervisor: único dueño del ciclo de vida de los targets servidos.

Por cada target registra un ``PipelineTarget`` (definición), ejecuta el
pipeline y guarda el contenido servido (M3U + XMLTV) **en memoria**: el único
servidor HTTP es el del demonio, que pregunta aquí por las rutas ``/weteve/...``.
El acceso desde la API/MCP llega por hilos distintos, así que los métodos
públicos van protegidos con dos locks:

- ``_lock``: estado del supervisor (targets, resultados, estado, servidores).
  Se sostiene poco y es el único que necesitan las lecturas (``get_status``,
  ``list_tv_channels``…), así que responder estado no espera a un pipeline.
- ``_run_lock``: ejecución del pipeline (3-8 s con fuentes grandes) y las
  cachés del propio ``ListManagerPipeline``, que no es reentrante.

Registro de fuentes: ``PipelineTarget.source_index`` es la ruta/URL M3U (modo
standalone "custom"). El EPG XMLTV asociado se pasa aparte (``epg_path``) y se
registra en el pipeline junto a la fuente, igual que hace el diálogo Qt.
"""

import logging
import re
import threading
import time
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from src.application.services.daemon.scheduler import RefreshScheduler, target_interval_min
from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.logo_store import LogoStore
from src.application.services.list_manager.models import (
    ChannelProfile,
    PipelineTarget,
)
from src.application.services.list_manager.pipeline import (
    ListManagerPipeline,
    PipelineResult,
    channel_identity,
    profile_of,
)
from src.application.services.list_manager.playlist_builder import (
    build_m3u,
    build_xmltv,
)
from src.application.services.list_manager.quality import base_name
from src.application.services.list_manager.stream_probe import StreamProbe
from src.infrastructure.web.playlist_server import LOGOS_PREFIX, url_base_for

logger = logging.getLogger(__name__)

#: Id de perfil aceptado en ``/m3u/weteve_<id>.m3u``: números en producción,
#: pero sin barras ni puntos para que no se pueda salir del directorio.
_ID_PERFIL = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _parece_codigo(channel_id: str, nombre: str) -> bool:
    """True si el id es un código de cadena (BBDRA, UCL) y no el nombre."""
    cid = str(channel_id or "").strip()
    visible = str(nombre or "").strip()
    if not cid or cid.lower() == visible.lower():
        return False
    if " " in cid or len(cid) > 8 or not cid.isupper():
        return False
    return True


def _nombre_con_codigo(channel_id: str, nombre: str) -> str:
    """Nombre del desplegable; añade el código interno si lo parece.

    Movistar usa el código de la cadena como id, y ese código sobrevive al
    cambio de marca: ``BBDRA`` hoy se llama «BBC Series» pero era «BBC Drama»,
    y ``UCL`` es «UEFA Champions League». Sin el código, buscar el canal por su
    nombre viejo en el desplegable es imposible.
    """
    visible = str(nombre or "").strip()
    if not _parece_codigo(channel_id, nombre):
        return visible or str(channel_id or "").strip()
    return f"{visible} ({channel_id})"

def _pagina(channels: list, total: int, offset: int) -> dict:
    """Forma única de una página de lectura.

    ``truncated`` dice si la respuesta no trae todo lo que hay: el cliente sabe
    que quedan filas (y que tiene que pedir otra página) en vez de creer que el
    catálogo es más pequeño de lo que es.
    """
    offset = int(offset)
    return {"channels": channels, "total": total, "count": len(channels),
            "offset": offset, "truncated": offset + len(channels) < total}


class TargetSupervisor:
    """Gestiona N targets: pipeline cacheado + servidor HTTP por target."""

    def __init__(self, catalog_fn=None, base_url: str = "",
                 logo_store: LogoStore | None = None,
                 stream_probe=None) -> None:
        # dos locks: estado (lecturas) y ejecución (pipeline). Ver docstring.
        self._lock = threading.RLock()
        self._run_lock = threading.RLock()
        self._pipeline = ListManagerPipeline()
        # copia local de los logos: la lista no depende de terceros
        self._logos = (logo_store if logo_store is not None
                       else LogoStore(self._render_default_logos_dir()))
        self._targets: dict[str, PipelineTarget] = {}
        self._epg_paths: dict[str, str | None] = {}
        # contenido servido por target: (playlist_m3u, epg_xml). Su presencia es
        # lo que significa "el target está sirviendo": no hay socket por target.
        self._content: dict[str, tuple[str, str]] = {}
        self._results: dict[str, PipelineResult] = {}  # último resultado por target
        # Contador por perfil: sube con cada cambio de lo que se sirve. La GUI
        # lo consulta en /status y solo repinta la tabla cuando cambia, en vez
        # de pedir las 855 filas cada segundo y medio.
        self._revision: dict[str, int] = {}
        self._state: dict[str, dict] = {}       # channel_count, error
        self._base_url = base_url.rstrip("/")
        self._port: int | None = None
        # cartera de fuentes: callable() -> SourceCatalog (inyectable p/ tests)
        self._catalog_fn = catalog_fn
        self._scheduler = RefreshScheduler(self._scheduled_reload)
        # tiempos de la última recarga por target (perfilado del flujo)
        self._timings: dict[str, dict] = {}
        # ¿responde todavía la línea del proveedor? Muestreo cacheado: /status
        # no puede quedarse esperando a un stream que no contesta.
        self._probe = stream_probe if stream_probe is not None else StreamProbe()

    # -- helpers internos ----------------------------------------------------

    @staticmethod
    def _render_default_logos_dir() -> Path:
        """``assets/logos`` en la raíz del proyecto."""
        return Path(__file__).resolve().parents[4] / "assets" / "logos"

    def _render(self, target, result, epg, target_id: str = "") -> tuple[str, str]:
        """Convierte un PipelineResult en (playlist, epg_xml) listos para servir.

        La playlist lleva ``url-tvg`` en la cabecera con la URL de su guía: es
        lo que permite a las apps descargarla solas, sin configurarla a mano.
        """
        t0 = time.perf_counter()
        canales = self._channels_with_local_logos(target, result.channels)
        url_tvg = None
        if self._base_url and str(target_id):
            url_tvg = f"{self._base_url}/xmltv/weteve_{target_id}.xml"
        playlist = build_m3u(canales, url_tvg=url_tvg)
        t1 = time.perf_counter()
        epg_xml = build_xmltv(result.channels, epg,
                              light=bool(getattr(target, "light_epg", True)))
        t2 = time.perf_counter()
        logger.info("render: playlist=%.2fs epg=%.2fs (%d canales servidos)",
                    t1 - t0, t2 - t1, len(result.channels))
        return playlist, epg_xml

    def _channels_with_local_logos(self, target, channels):
        """Sustituye la URL remota del logo por la copia que sirve el demonio.

        Un logo de un tercero puede cambiar o desaparecer; el que ya está en
        ``assets/logos`` se sirve desde ``/logos/<fichero>``, así que la lista
        que ve el reproductor depende solo de este equipo. Los que no se
        pueden bajar conservan su URL remota.
        """
        urls = [ch.logo_url for ch in channels if ch.logo_url]
        if not urls or self._logos is None:
            return channels
        locales = self._logos.ensure(urls)
        if not locales:
            return channels
        # La lista la sirve el demonio (``/m3u/weteve_<id>.m3u``), no el
        # puerto histórico del target: el logo tiene que apuntar al mismo sitio
        # desde el que el reproductor descarga la lista.
        base = self._base_url or url_base_for(
            getattr(target, "bind", "") or "127.0.0.1",
            int(getattr(target, "port", 0) or 0))
        salida = []
        for ch in channels:
            # Un logo elegido a mano ya está en assets/logos: se sirve tal cual,
            # sin descargarlo ni pasar por el cache.
            if ch.logo_file:
                salida.append(dataclasses_replace(
                    ch, logo_url=f"{base}{LOGOS_PREFIX}{ch.logo_file}"))
            elif ch.logo_url in locales:
                salida.append(dataclasses_replace(
                    ch, logo_url=f"{base}{LOGOS_PREFIX}{locales[ch.logo_url]}"))
            else:
                salida.append(ch)
        return salida

    def _ensure_source(self, target: PipelineTarget, epg_path: str | None,
                       refresh_epg: bool = False) -> None:
        """Registra la(s) fuente(s) del target en el pipeline.

        Con cartera (source_refs) registra la unión multi-fuente; sin ella,
        el flujo antiguo de fuente única (custom_m3u/custom_epg).

        ``refresh_epg`` solo lo piden el planificador y la actualización
        manual: cambiar reglas o mapeos reutiliza la guía ya cargada.
        """
        if target.source_refs:
            self._pipeline.register_merged(target.source_index,
                                           target.source_refs, target.epg_refs,
                                           refresh_epg=refresh_epg)
        else:
            m3u = str(target.source_index)
            self._pipeline.register_source(target.source_index, m3u, epg_path,
                                           refresh_epg=refresh_epg)

    def _remember(self, target_id: str, result) -> None:
        self._results[target_id] = result
        self._revision[target_id] = self._revision.get(target_id, 0) + 1
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

    def set_base_url(self, base_url: str, port: int | None = None) -> None:
        """Fija la URL pública con la que se anuncian playlist y EPG.

        El puerto lo conoce quien enlaza el socket (``build_daemon``), no el
        supervisor: él solo compone las rutas.
        """
        self._base_url = base_url.rstrip("/")
        self._port = port

    def playlist_route(self, path: str) -> tuple[int, str | bytes, str] | None:
        """Resuelve una ruta de guía/lista/logo: (status, cuerpo, content-type).

        Formato único, el que usan xTeVe y Threadfin (y reconocen sus apps):

        - ``/m3u/weteve_<perfil>.m3u``
        - ``/xmltv/weteve_<perfil>.xml``

        Los logos salen de ``assets/logos`` (``/logos/<fichero>``) y se
        devuelven como bytes, porque son imágenes. ``None`` si la ruta no es
        de este servidor.
        """
        clean = (path or "").split("?", 1)[0].rstrip("/")
        parts = [p for p in clean.split("/") if p]
        if "logos" in parts:
            indice = parts.index("logos")
            if len(parts) != indice + 2:
                return None
            return self.logo_route(parts[indice + 1])
        if len(parts) != 2:
            return None
        carpeta, fichero = parts
        if carpeta == "m3u" and fichero.startswith("weteve_") \
                and fichero.endswith(".m3u"):
            target_id, contenido = fichero[len("weteve_"):-len(".m3u")], "playlist"
        elif carpeta == "xmltv" and fichero.startswith("weteve_") \
                and fichero.endswith(".xml"):
            target_id, contenido = fichero[len("weteve_"):-len(".xml")], "epg"
        else:
            return None
        if not _ID_PERFIL.fullmatch(target_id):
            return None
        with self._lock:
            par = self._content.get(target_id)
        if par is None:
            return 404, "perfil no disponible", "text/plain; charset=utf-8"
        playlist, epg_xml = par
        if contenido == "playlist":
            return 200, playlist, "audio/x-mpegurl"
        return 200, epg_xml, "application/xml; charset=utf-8"


    def logo_route(self, nombre: str) -> tuple[int, bytes | str, str]:
        """Logo local por nombre de fichero (sin salirse del directorio)."""
        import mimetypes
        from urllib.parse import unquote

        limpio = unquote(nombre or "").split("?")[0].strip()
        if not limpio or "/" in limpio or "\\" in limpio or ".." in limpio:
            return 404, "logo no disponible", "text/plain; charset=utf-8"
        try:
            datos = (self._logos.directory / limpio).read_bytes()
        except OSError:
            return 404, "logo no disponible", "text/plain; charset=utf-8"
        tipo = mimetypes.guess_type(limpio)[0] or "application/octet-stream"
        return 200, datos, tipo

    def _scheduled_reload(self, target_id: str) -> None:
        """Ciclo del scheduler: reejecutar el pipeline releyendo las guías."""
        self.reload_target(target_id, refresh_epg=True)

    # -- ciclo de vida -------------------------------------------------------

    def start_target(
        self, target_id: str, target: PipelineTarget, epg_path: str | None = None,
    ) -> bool:
        """Ejecuta el pipeline del target y deja su contenido listo para servir.

        No abre puerto: el servidor es único y lo tiene el demonio, que consulta
        ``playlist_route``.
        """
        with self._run_lock:
            with self._lock:
                self._targets[target_id] = target
                self._epg_paths[target_id] = epg_path
            try:
                self._ensure_source(target, epg_path)
                result = self._pipeline.run(target)
            except Exception as e:
                logger.error("Pipeline falló para %s: %s", target_id, e)
                with self._lock:
                    self._state[target_id] = {"error": str(e)}
                    self._drop_content(target_id)
                return False
            epg = self._pipeline.get_epg(target.source_index)
            content = self._render(target, result, epg, target_id)
            with self._lock:
                self._remember(target_id, result)
                self._schedule_refresh(target_id, target)
                self._content[target_id] = content
            return True

    def stop_target(self, target_id: str) -> bool:
        """Para el servidor del target; False si no estaba arrancado."""
        with self._lock:
            self._scheduler.cancel(target_id)
            return self._drop_content(target_id)

    def _drop_content(self, target_id: str) -> bool:
        """Deja de servir el target (el pipeline y su cache se conservan)."""
        habia = self._content.pop(target_id, None) is not None
        if habia:
            # lo servido cambió (a nada): la tabla del perfil debe repintarse
            self._revision[target_id] = self._revision.get(target_id, 0) + 1
        return habia

    def reload_target(
        self, target_id: str, target: PipelineTarget | None = None,
        epg_path: str | None = None, refresh_epg: bool = False,
    ) -> bool:
        """Reejecuta el pipeline; si el target estaba sirviendo, actualiza el contenido.

        Con ``target`` None reutiliza la definición registrada. Un reload sobre
        un target no arrancado construye la cache pero no abre puerto.
        """
        with self._run_lock:
            with self._lock:
                tgt = target or self._targets.get(target_id)
                if tgt is None:
                    return False
                self._targets[target_id] = tgt
                if epg_path is not None or target is not None:
                    self._epg_paths[target_id] = (
                        epg_path or self._epg_paths.get(target_id))
                current_epg = self._epg_paths.get(target_id)
            try:
                t_fuentes = time.perf_counter()
                self._ensure_source(tgt, current_epg, refresh_epg=refresh_epg)
                t_pipeline = time.perf_counter()
                result = self._pipeline.run(tgt)
                t_run = time.perf_counter()
            except Exception as e:
                logger.error("Pipeline falló al recargar %s: %s", target_id, e)
                with self._lock:
                    self._state[target_id] = {"error": str(e)}
                return False
            epg = self._pipeline.get_epg(tgt.source_index)
            content = self._render(tgt, result, epg, target_id)
            t_render = time.perf_counter()
            self._timings[target_id] = {
                "fuentes": round(t_pipeline - t_fuentes, 3),
                "pipeline": round(t_run - t_pipeline, 3),
                "render": round(t_render - t_run, 3),
                "total": round(t_render - t_fuentes, 3),
                "refresh_epg": bool(refresh_epg),
            }
            logger.info(
                "recarga %s: fuentes=%.2fs pipeline=%.2fs render=%.2fs "
                "total=%.2fs (guias %s)", target_id,
                t_pipeline - t_fuentes, t_run - t_pipeline,
                t_render - t_run, t_render - t_fuentes,
                "releidas" if refresh_epg else "de cache")
            with self._lock:
                self._remember(target_id, result)
                self._schedule_refresh(target_id, tgt)
                if target_id in self._content:  # solo si ya se estaba sirviendo
                    self._content[target_id] = content
            return True

    def stop_all(self) -> None:
        with self._lock:
            self._scheduler.cancel_all()
            self._content.clear()

    def remove_target(self, target_id: str) -> bool:
        """Elimina la definición del supervisor, parando su servidor si corría."""
        with self._lock:
            self._scheduler.cancel(target_id)
            self._drop_content(target_id)
            existed = self._targets.pop(target_id, None) is not None
            self._results.pop(target_id, None)
            self._state.pop(target_id, None)
            self._epg_paths.pop(target_id, None)
            return existed

    # -- consultas -------------------------------------------------------------

    def has_snapshot(self, target_id: str) -> bool:
        """True si el target ya tiene un resultado de pipeline en memoria."""
        with self._lock:
            return target_id in self._results

    def _canales_del_snapshot(self, target_id: str) -> list:
        """Canales del último pipeline del perfil (servidos y descartados)."""
        with self._lock:
            result = self._results.get(target_id)
            if result is None:
                return []
            return (list(result.channels) + list(result.excluded_no_epg)
                    + list(result.excluded_disabled)
                    + list(result.excluded_duplicate))

    def url_identities(self, target_id: str) -> dict[str, str]:
        """``url -> identidad`` del último snapshot.

        Es lo que permite guardar una decisión que llega por url (UI, MCP,
        API) en la ficha de su canal. Vacío si el pipeline no ha corrido: en
        ese caso la decisión se guarda en el formato viejo, que es lo seguro.
        """
        return {c.url: channel_identity(c.canonical_id, c.url, c.name)
                for c in self._canales_del_snapshot(target_id) if c.url}

    def urls_for_identity(self, target_id: str) -> dict[str, list[str]]:
        """``identidad -> [urls]`` del último snapshot (el inverso)."""
        salida: dict[str, list[str]] = {}
        for url, identidad in self.url_identities(target_id).items():
            salida.setdefault(identidad, []).append(url)
        return salida

    def mapping_candidates(self, target_id: str, limit: int = 200,
                           offset: int = 0,
                           include_served: bool = False) -> dict:
        """Canales de la tabla de mapeo, desde el último snapshot.

        Por defecto, solo los que necesitan atención: los descartados por
        falta de guía, los desactivados a mano y los servidos sin guía. Con
        ``include_served`` se añaden los servidos con guía (los que ya
        funcionan), para poder revisarles o cambiarles la EPG. No reejecuta el
        pipeline: si nunca se ejecutó, devuelve vacío.
        """
        epg = None
        target = None
        with self._lock:
            result = self._results.get(target_id)
            if result is None:
                return _pagina([], 0, offset)
            target = self._targets.get(target_id)
            if target is not None:
                epg = self._pipeline.get_epg(target.source_index)
            disabled = {ch.url for ch in result.excluded_disabled}
            servidos = {c.url for c in result.channels}
            duplicados = {c.url for c in result.excluded_duplicate}
            pendientes = (list(result.excluded_no_epg)
                          + list(result.excluded_disabled)
                          + list(result.excluded_duplicate)
                          + [c for c in result.channels
                             if c.matched_epg_id is None])
            candidates = list(result.channels) if include_served else []
            rows = []
            seen: set[str] = set()
            info = epg.channel_info if epg is not None else {}
            nombres = epg.channel_names if epg is not None else {}
            for ch in (pendientes + candidates):
                if ch.url in seen:
                    continue
                seen.add(ch.url)
                interno = ch.internal_name or ch.name
                canonico = ch.canonical_id or ""
                meta = info.get(canonico)
                de_guia = nombres.get(canonico) or ""
                # la guía solo publica el código como nombre: no se usa
                if de_guia == canonico:
                    de_guia = ""
                perfil = (profile_of(target, canonico, ch.url, interno)
                          if target is not None else ChannelProfile())
                # La tabla pinta ``logo_url``: si hay logo elegido a mano se
                # publica el que sirve el demonio, no la URL remota (que puede
                # estar muerta, como las de orangetv.orange.es).
                logo = (f"{self._base_url}/logos/{ch.logo_file}"
                        if ch.logo_file and self._base_url else ch.logo_url)
                rows.append({
                    "name": ch.name,
                    "internal_name": interno,
                    "url": ch.url,
                    "group": ch.group,
                    "tvg_id": ch.matched_epg_id or "",
                    "logo_url": logo or "",
                    "logo_file": ch.logo_file or "",
                    "disabled": ch.url in disabled,
                    # el daemon decide si la fila se sirve: un duplicado de
                    # calidad tiene guía pero no sale en la lista
                    "served": ch.url in servidos,
                    "duplicate": ch.url in duplicados,
                    # lo que la tabla necesita para editar sin adivinar
                    "identity": channel_identity(canonico, ch.url, interno),
                    "dial": ch.chno,
                    "dial_auto": meta.dial if meta is not None else None,
                    "display_auto": de_guia,
                    "profile": {"dial": perfil.dial,
                                "display": perfil.display,
                                "logo": perfil.logo},
                })
        # Orden de la tabla: por número los que lo tienen (el mismo orden que
        # verá el reproductor) y al final los que aún no tienen dial.
        rows.sort(key=lambda r: (r["dial"] is None, r["dial"] or 0,
                                 str(r["internal_name"] or "").lower()))
        total = len(rows)
        page = rows[int(offset):int(offset) + int(limit)]
        return _pagina(page, total, offset)

    def get_status(self) -> dict:
        """Estado agregado: {"targets": {id: {...}}} con running/URLs/errores."""
        with self._lock:
            out: dict[str, dict] = {}
            for tid, tgt in self._targets.items():
                st = dict(self._state.get(tid, {}))
                running = tid in self._content
                playlist_url = epg_url = None
                if running and self._base_url:
                    playlist_url = f"{self._base_url}/m3u/weteve_{tid}.m3u"
                    epg_url = f"{self._base_url}/xmltv/weteve_{tid}.xml"
                resultado = self._results.get(tid)
                out[tid] = {
                    "name": tgt.name,
                    "running": running,
                    "port": self._port,
                    "channel_count": st.get("channel_count"),
                    "error": st.get("error"),
                    "playlist_url": playlist_url,
                    "epg_url": epg_url,
                    # cambia con cada guardado o recarga de este perfil
                    "revision": self._revision.get(tid, 0),
                    # «N de M streams responden»: muestreo barato del origen
                    "streams": self._probe.status(
                        tid, [c.url for c in resultado.channels]
                        if resultado is not None else []),
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
                return _pagina([], 0, offset)
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
        return _pagina(page, total, offset)

    def list_epg_channels(self, target_id: str, limit: int = 200,
                          offset: int = 0) -> dict:
        """Canales del XMLTV del target: [{'id', 'display_name', 'lists'}].

        ``lists`` son las guías que anuncian el canal (una o varias: la unión
        de fuentes puede duplicar el mismo id en dos guías). Requiere que el
        target tenga EPG asignado y cargado (reload/run_pipeline previo).
        Paginado para no volcar catálogos enormes.
        """
        epg = None
        lists: dict[str, tuple[str, ...]] = {}
        with self._lock:
            target = self._targets.get(target_id)
            if target is not None:
                epg = self._pipeline.get_epg(target.source_index)
                lists = self._pipeline.epg_lists(target.source_index)
        if epg is None:
            return {"error": (f"target {target_id} sin EPG cargado: "
                               "ejecuta reload_target o run_pipeline")}
        items = sorted(epg.channel_names.items())
        total = len(items)
        page = items[int(offset):int(offset) + int(limit)]
        return _pagina(
            [{"id": cid, "display_name": _nombre_con_codigo(cid, name),
              "lists": list(lists.get(cid, ()))}
             for cid, name in page], total, offset)

    def epg_catalog(self, target_id: str, limit: int = 200,
                    offset: int = 0) -> dict:
        """Tabla de referencia de la guía: dial, nombre, código y guías.

        Sale de los metadatos de canal que publica la guía (``channel_info``),
        no de la lista servida: es el dialecto real del proveedor, con sus
        códigos (``BBDRA``, ``UCL``) y sus huecos. ``served`` dice qué canales
        de ese catálogo están hoy en la lista del target, por identidad
        canónica o por el id que resolvió la parrilla.
        """
        epg = None
        lists: dict[str, tuple[str, ...]] = {}
        servidos: set[str] = set()
        nombres_servidos: set[str] = set()
        with self._lock:
            target = self._targets.get(target_id)
            if target is not None:
                epg = self._pipeline.get_epg(target.source_index)
                lists = self._pipeline.epg_lists(target.source_index)
                result = self._results.get(target_id)
                if result is not None:
                    # «En la lista» va por identidad: el canal canónico cuenta
                    # aunque su parrilla venga de otra guía (un mapeo a otra guía
                    # dejaba el canal canónico como si no estuviera).
                    servidos = {c.matched_epg_id for c in result.channels
                                if c.matched_epg_id}
                    servidos |= {c.canonical_id for c in result.channels
                                 if c.canonical_id}
                    # Un mapeo manual puede apuntar a una guía con otro
                    # nombre: el canal sigue estando «en la lista» por nombre.
                    nombres_servidos = {base_name(c.name)
                                        for c in result.channels}
        if epg is None:
            return {"error": (f"target {target_id} sin EPG cargado: "
                                "ejecuta reload_target o run_pipeline")}
        info = epg.channel_info
        filas = []
        for cid, nombre in epg.channel_names.items():
            meta = info.get(cid)
            en_lista = cid in servidos or base_name(nombre) in nombres_servidos
            filas.append({
                "id": cid,
                "name": nombre,
                "code": cid if _parece_codigo(cid, nombre) else "",
                "dial": meta.dial if meta is not None else None,
                "logo": (meta.logo if meta is not None else None) or "",
                "lists": list(lists.get(cid, ())),
                "served": en_lista,
            })
        filas.sort(key=lambda f: (f["dial"] is None, f["dial"] or 0,
                                  str(f["name"]).lower()))
        total = len(filas)
        page = filas[int(offset):int(offset) + int(limit)]
        return _pagina(page, total, offset)

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
        with self._run_lock:
            with self._lock:
                tgt = self._targets.get(target_id)
            if tgt is None:
                return {"error": f"target {target_id} no existe"}
            try:
                result = self._pipeline.run(tgt)
            except Exception as e:
                return {"error": str(e)}
            with self._lock:
                self._remember(target_id, result)
            return {
                "total_input": result.total_input,
                "filtered_count": result.filtered_count,
                "unmatched_count": len(result.unmatched),
            }

    def groups(self, target_id: str) -> list[str]:
        """Grupos de la fuente del target (para los combos de las reglas).

        Usa la cache de canales del pipeline: tras una ejecución no vuelve a
        leer la fuente.
        """
        with self._lock:
            tgt = self._targets.get(target_id)
        if tgt is None:
            return []
        return self._pipeline.get_groups(tgt.source_index)

    def group_fields(self, target_id: str) -> dict[str, list[str]]:
        """Campos de agrupación de la fuente (group-title, tvg-group, ...)."""
        with self._lock:
            tgt = self._targets.get(target_id)
        if tgt is None:
            return {}
        return self._pipeline.get_group_fields(tgt.source_index)

    def preview_filter(self, target_id: str, rules) -> dict:
        """Canales que sobreviven a unas reglas, sin ejecutar el pipeline.

        Es la vista previa de la pestaña de filtros: reutiliza la cache de
        canales, así que no re-descarga la fuente.
        """
        with self._lock:
            tgt = self._targets.get(target_id)
        if tgt is None:
            return {"error": f"target {target_id} no existe"}
        kept, total = self._pipeline.preview_filter(tgt.source_index, list(rules))
        return {"channels": [c.name for c in kept], "count": len(kept),
                "total": total}

    def metrics(self, target_id: str) -> dict:
        """Métricas del último pipeline ejecutado, sin reejecutarlo."""
        with self._lock:
            result = self._results.get(target_id)
            error = self._state.get(target_id, {}).get("error")
        if result is None:
            return {"error": error or f"target {target_id} sin ejecutar"}
        out = {
            "total_input": result.total_input,
            "filtered_count": result.filtered_count,
            "unmatched_count": len(result.unmatched),
            "duplicates_count": len(result.excluded_duplicate),
            "timings": dict(self._timings.get(target_id, {})),
        }
        if error:
            out["error"] = error
        return out
