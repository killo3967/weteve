"""Pipeline del gestor de listas: parse -> filtros -> match EPG -> salida."""

import logging
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from src.application.services.list_manager.dummy_epg import dummy_id_for
from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import (
    ChannelProfile,
    FilterRule,
    MappedChannel,
    PipelineTarget,
    channel_keys,
    merge_profiles,
)
from src.application.services.list_manager.quality import base_name, dedupe_variants
from src.domain.entities.epg import ChannelInfo, EPGData, Program
from src.domain.ports.i_epg_repo import IEPGRepository
from src.infrastructure.adapters.epg_repository_router import EPGRepositoryRouter
from src.infrastructure.adapters.file_m3u_repository import FileM3URepository

logger = logging.getLogger(__name__)


def _combinar(principal: ChannelInfo | None,
              respaldo: ChannelInfo | None) -> ChannelInfo | None:
    """Une dos metadatos del mismo canal: cada campo se queda con el que hay."""
    if principal is None:
        return respaldo
    if respaldo is None:
        return principal
    return ChannelInfo(
        dial=principal.dial if principal.dial is not None else respaldo.dial,
        logo=principal.logo or respaldo.logo)


def profile_of(target: PipelineTarget, canonical: str | None,
               url: str = "", name: str = "") -> ChannelProfile:
    """Ficha del canal: la de su código de guía, la de su url o la de su nombre.

    Un canal puede tener más de una (un mapeo manual antiguo dejó una por url);
    se funden campo a campo.
    """
    fichas = [target.channels[clave] for clave
              in channel_keys(canonical, url, name) if clave in target.channels]
    return merge_profiles(fichas)


def channel_identity(canonical: str | None, url: str = "",
                     name: str = "") -> str:
    """Identidad estable del canal: código de guía, url o nombre."""
    claves = channel_keys(canonical, url, name)
    return claves[0] if claves else ""


def _is_separator_channel(name: str) -> bool:
    """True si el nombre es en realidad un separador de sesion, no un canal.

    Varias listas codifican cabeceras de seccion como canales cuyo display
    name empieza por '#' ("### SUP MOVIES ###", "#### NEWS ####"). No son
    reproducibles como tal y el usuario los quiere fuera de la salida siempre.
    Solo cuenta el prefijo: un nombre con '#' intermedia ("Tele Mundo #1")
    es un canal legitimo.
    """
    return name.lstrip().startswith("#")


class PipelineResult:
    """Resultado de ejecutar el pipeline sobre un target."""

    def __init__(
        self,
        channels: list[MappedChannel],
        total_input: int,
        unmatched: list[str],
        excluded_disabled: list[MappedChannel] | None = None,
        excluded_no_epg: list[MappedChannel] | None = None,
        require_epg: bool = False,
        excluded_duplicate: list[MappedChannel] | None = None,
    ):
        self.channels = channels
        self.total_input = total_input
        self.unmatched = unmatched  # nombres de canales sin EPG
        # Canales que pasaron los filtros pero están desactivados a mano: no
        # se sirven, pero la UI los necesita para poder reactivarlos.
        self.excluded_disabled = list(excluded_disabled or [])
        # Canales descartados por la regla de servidor (sin guía asignada).
        # Se conservan a propósito: son la única fuente de filas de la tabla de
        # mapeo, y sin ellas el usuario no podría asignarles Dummy 1h para
        # rescatarlos. Nunca se sirven en el M3U.
        self.excluded_no_epg = list(excluded_no_epg or [])
        # Variantes del mismo canal (mismo id EPG y mismo nombre base) que no
        # se sirven porque otra tiene más prioridad de calidad. Se conservan
        # para que la tabla de mapeo las muestre y el usuario vea por qué no
        # están en la lista.
        self.excluded_duplicate = list(excluded_duplicate or [])
        # True si había fuente EPG y, por tanto, la regla se ha aplicado.
        self.require_epg = require_epg

    @property
    def filtered_count(self) -> int:
        return len(self.channels)


class ListManagerPipeline:
    """Orquesta el procesamiento de un ``PipelineTarget``.

    Separa la obtención de datos (repositorios, inyectables para tests) del
    procesamiento puro, para poder testear sin red ni ficheros.
    """

    def __init__(
        self,
        m3u_repository: FileM3URepository | None = None,
        epg_repository: IEPGRepository | None = None,
    ):
        self._m3u_repo = m3u_repository or FileM3URepository()
        self._epg_repo = epg_repository or EPGRepositoryRouter()
        # cache de fuentes resueltas por clave: índice (modo app) o ruta (custom)
        self._sources: dict[int | str, tuple[str, EPGData | None]] = {}
        # cartera: refs de un target multi-fuente (clave -> (source_refs, epg_refs))
        self._merged: dict[int | str, tuple[tuple, tuple]] = {}
        # EPG fusionada por clave multi-fuente (calculada en register_merged)
        self._merged_epg: dict[int | str, EPGData | None] = {}
        # Guía(s) que anuncian cada channel-id: {clave: {id: (lista, ...)}}
        self._epg_lists: dict[int | str, dict[str, tuple[str, ...]]] = {}
        # cache de canales por fuente para get_groups (evita re-descargar)
        self._channel_cache: dict[int | str, list] = {}
        # ruta de guía registrada por fuente (fuente única): detecta cambios
        self._source_epg_path: dict[int | str, str] = {}
        # cache canal→guía: no depende de las reglas, así que sobrevive a las
        # recargas (se invalida si cambia la guía o los overrides)
        self._match_cache: dict[
            int | str, tuple[tuple, dict[tuple, tuple[str, str]]]] = {}

    def register_source(self, index: int | str, m3u_path: str,
                        epg_path: str | None,
                        refresh_epg: bool = False) -> None:
        """Registra la fuente M3U y su EPG opcional.

        ``refresh_epg=False`` (por defecto) reutiliza la guía ya cargada: un
        cambio de reglas o de mapeos no tiene por qué volver a descargarla.
        Con ``refresh_epg=True`` (planificador, botón «Actualizar guías») se
        vuelve a leer.
        """
        # registrar invalida la cache de canales: el fichero/URL puede haber
        # cambiado desde la última lectura y la vista previa debe ver lo mismo
        # que el pipeline completo.
        self._channel_cache.pop(index, None)
        previous = self._sources.get(index)
        epg_previa = previous[1] if previous is not None else None
        ruta_previa = self._source_epg_path.get(index)
        self._source_epg_path[index] = str(epg_path or "")
        self._sources[index] = (m3u_path, None)
        if not epg_path:
            self._epg_lists.pop(index, None)
            return
        if epg_previa is not None and not refresh_epg                 and ruta_previa == str(epg_path):
            self._sources[index] = (m3u_path, epg_previa)
            return
        epg = self._epg_repo.load_epg(epg_path, fresh=refresh_epg)
        self._sources[index] = (m3u_path, epg)
        if epg is not None:
            label = Path(str(epg_path)).name or str(epg_path)
            self._epg_lists[index] = {cid: (label,)
                                      for cid in epg.channel_names}

    def register_merged(self, index: int | str, source_refs: tuple,
                        epg_refs: tuple, refresh_epg: bool = False) -> None:
        """Registra un target multi-fuente (cartera global de listas).

        ``source_refs``/``epg_refs`` son tuplas de SourceRef (id, name,
        location). Los canales no se descargan hasta el primer uso; la EPG
        se fusiona en un solo EPGData (unión por id de canal).

        La guía se reutiliza salvo que ``refresh_epg`` lo pida o que cambien
        las fuentes de guía: filtrar un canal no puede costar una descarga.
        """
        self._channel_cache.pop(index, None)
        previous = self._merged.get(index)
        mismas_guias = previous is not None and tuple(epg_refs) == previous[1]
        self._merged[index] = (tuple(source_refs), tuple(epg_refs))
        if (refresh_epg or not mismas_guias
                or index not in self._merged_epg):
            # la EPG fusionada se calcula aquí (register_source también carga
            # la suya de forma anticipada): run() y get_epg() la leen de cache
            self._merged_epg[index], self._epg_lists[index] = self._merge_epgs(
                self._merged[index][1], fresh=refresh_epg)
        # clave visible en _sources para que run()/get_groups() la encuentren;
        # el primer location sirve de placeholder para la carga por clave.
        self._sources.setdefault(index,
                                 (source_refs[0].location if source_refs else "",
                                  None))

    def get_groups(self, source: int | str | None = None) -> list[str]:
        """Grupos distintos de una fuente, ordenados y sin duplicados.

        Args:
            source: clave de la fuente (índice o ruta). None = última usada.

        Usa la cache de canales si está; si no, los carga del repositorio.
        """
        if source is None:
            if not self._sources:
                return []
            source = next(iter(self._sources))
        if source not in self._sources:
            return []
        channels = self._channel_cache.get(source)
        if channels is None:
            channels = self._m3u_repo.get_channels(self._sources[source][0])
            self._channel_cache[source] = channels
        groups = sorted({c.group for c in channels if c.group and c.group.strip()})
        return groups

    def get_group_fields(self, source: int | str | None = None) -> dict[str, list[str]]:
        """Campos de agrupación disponibles en una fuente y sus valores.

        Devuelve {nombre_de_campo: [valores ordenados]} a partir de
        ``Channel.groups``: p. ej. {"group-title": [...], "tvg-group": [...],
        "extgrp": [...]}. Solo incluye campos con al menos un valor.
        """
        channels = self._channels_of(source)
        fields: dict[str, set[str]] = {}
        for ch in channels:
            for fname, values in (ch.groups or {}).items():
                fields.setdefault(fname, set()).update(
                    v for v in values if v and v.strip())
        return {k: sorted(v) for k, v in sorted(fields.items())}

    def _channels_of(self, source: int | str | None) -> list:
        """Canales de una fuente registrada, usando la cache."""
        if source is None:
            if not self._sources:
                return []
            source = next(iter(self._sources))
        if source not in self._sources:
            return []
        channels = self._channel_cache.get(source)
        if channels is None:
            channels = self._load_channels(source)
            self._channel_cache[source] = channels
        return channels

    def _load_channels(self, source: int | str) -> list:
        """Carga canales de una clave: unión multi-fuente o fuente única."""
        merged = self._merged.get(source)
        if merged is None:
            return self._m3u_repo.get_channels(self._sources[source][0])
        source_refs, _ = merged
        out: list = []
        for ref in source_refs:
            for ch in self._m3u_repo.get_channels(ref.location):
                # etiqueta la lista de origen para el filtro 'Lista' y el
                # prefijo de render (groups es mutable en Channel)
                ch.groups.setdefault("source", []).append(ref.name)
                out.append(ch)
        return out

    def preview_filter(
        self, source: int | str, rules: list[FilterRule] | tuple[FilterRule, ...],
    ) -> tuple[list, int]:
        """Canales que sobreviven a las reglas de filtro y total de la fuente.

        Aplica solo la etapa de filtros (sin match EPG ni canales desactivados):
        es la vista previa en vivo de la pestaña de reglas, que no debe pagar el
        coste del pipeline completo en cada pulsación. Reutiliza la cache de
        canales, así que no vuelve a descargar la fuente.

        Returns:
            (canales_que_pasan, total_de_canales_de_la_fuente).
        """
        try:
            channels = self._channels_of(source)
        except Exception:  # noqa: BLE001 - fuente ilegible: preview vacía
            logger.warning("Vista previa: fuente %s ilegible", source, exc_info=True)
            return [], 0
        active = list(rules)
        kept = [
            ch for ch in channels
            if not _is_separator_channel(ch.name)
            and evaluate_channel(ch.name, ch.group, ch.url, active,
                                 channel_groups=ch.groups)
        ]
        return kept, len(channels)

    def get_epg(self, index: int | str) -> EPGData | None:
        """Devuelve la EPG cacheada de una fuente registrada, o None."""
        if index in self._merged:
            return self._merged_epg.get(index)
        entry = self._sources.get(index)
        return entry[1] if entry else None

    def epg_lists(self, index: int | str) -> dict[str, tuple[str, ...]]:
        """Guía(s) que anuncian cada channel-id de la EPG del target.

        Con varias guías activas la unión es lo que se sirve, pero el usuario
        necesita saber de cuál viene cada canal (y cuáles lo anuncian las dos).
        """
        return dict(self._epg_lists.get(index, {}))

    def run(self, target: PipelineTarget) -> PipelineResult:
        """Ejecuta el pipeline completo sobre el target."""
        if target.source_index not in self._sources:
            raise ValueError(f"Fuente {target.source_index} no registrada")

        merged = self._merged.get(target.source_index)
        if merged is not None:
            return self._run_merged(target, merged)
        m3u_path, epg = self._sources[target.source_index]
        return self._run_single(target, m3u_path, epg)

    def _run_merged(self, target: PipelineTarget,
                    merged: tuple[tuple, tuple]) -> PipelineResult:
        """Flujo multi-fuente: unión de listas activas del target."""
        source_refs, epg_refs = merged
        raw_channels = self._channels_of(target.source_index)
        total_input = len(raw_channels)

        epg = self._merged_epg.get(target.source_index)
        kept: list[MappedChannel] = []
        for ch in raw_channels:
            if _is_separator_channel(ch.name):
                continue
            rules = list(target.rules)
            if not evaluate_channel(ch.name, ch.group, ch.url, rules,
                                     channel_groups=ch.groups):
                continue
            kept.append(
                MappedChannel(
                    name=ch.name,
                    url=ch.url,
                    group=ch.group,
                    logo_url=ch.logo_url,
                    tvg_id=ch.tvg_id,
                    chno=None,
                    source_name=(ch.groups.get("source") or [""])[0],
                )
            )
        return self._finish(target, kept, total_input, epg)

    def _merge_epgs(self, epg_refs: tuple, fresh: bool = False
                    ) -> tuple[EPGData | None, dict[str, tuple[str, ...]]]:
        """Unión de las guías activas: programas suman y nombres se completan.

        Debe construirse un único EPGData al final: el agregado indexa los
        programas por canal/nombre solo en el constructor.

        Devuelve además, por channel-id, las guías que lo anuncian (el nombre
        de la fuente, no su ruta): la tabla de mapeo lo muestra y así dos
        fuentes con el mismo nombre de canal dejan de ser indistinguibles.

        Las guías se leen en orden de prioridad (mayor primero): el primer
        nombre que llega es el que gana en el emparejamiento, así que ese
        orden es el que decide qué guía manda cuando dos anuncian el mismo
        canal. Cada id guarda además el peso de su guía, que el matcher usa
        para desempatar entre candidatos de guías distintas.
        """
        if not epg_refs:
            return None, {}
        programs: list[Program] = []
        names: dict[str, str] = {}
        info: dict[str, ChannelInfo] = {}
        priorities: dict[str, int] = {}
        origins: dict[str, list[str]] = {}
        ordenadas = sorted(
            epg_refs,
            key=lambda r: -int(getattr(r, "priority", 0) or 0))
        for ref in ordenadas:
            epg = self._epg_repo.load_epg(ref.location, fresh=fresh)
            if epg is None:
                continue
            peso = int(getattr(ref, "priority", 0) or 0)
            label = str(getattr(ref, "name", "") or "") or Path(
                str(getattr(ref, "location", ""))).name
            programs.extend(epg.programs)
            for cid, name in epg.channel_names.items():
                if name and not names.get(cid):
                    names[cid] = name
                if label and label not in origins.setdefault(cid, []):
                    origins[cid].append(label)            # Dial y logo: gana el primero que los publique, y una segunda
            # guía puede completar el que falte (una trae dial y otra logo).
            for cid, meta in epg.channel_info.items():
                previo = info.get(cid) or ChannelInfo()
                info[cid] = ChannelInfo(
                    dial=previo.dial if previo.dial is not None else meta.dial,
                    logo=previo.logo or meta.logo,
                )
            for cid in epg.channel_names:
                previa = priorities.get(cid)
                if previa is None or peso > previa:
                    priorities[cid] = peso
        lists = {cid: tuple(labels) for cid, labels in origins.items()}
        if not programs and not names:
            return None, lists
        return EPGData(programs=programs, channel_names=names,
                       channel_info=info,
                       channel_priority=priorities), lists

    def _run_single(self, target: PipelineTarget, m3u_path: str,
                    epg: EPGData | None) -> PipelineResult:
        """Flujo antiguo de fuente única (sin cartera)."""

        raw_channels = self._channels_of(target.source_index)
        total_input = len(raw_channels)

        kept: list[MappedChannel] = []
        for ch in raw_channels:
            # Los separadores de sección escritos como canales ("### TOP ###",
            # "#### NEWS ####") no son canales: se descartan siempre, sin
            # regla explicita. (Las LINEAS # ya las ignora el parser M3U.)
            if _is_separator_channel(ch.name):
                continue
            rules = list(target.rules)
            # channel_groups lleva los campos de agrupación reales (group-title
            # múltiples, tvg-group, #EXTGRP). Sin él, una regla de grupo solo
            # ve el grupo primario y los campos dinámicos no filtran nada.
            if not evaluate_channel(ch.name, ch.group, ch.url, rules,
                                     channel_groups=ch.groups):
                continue
            kept.append(
                MappedChannel(
                    name=ch.name,
                    url=ch.url,
                    group=ch.group,
                    logo_url=ch.logo_url,
                    tvg_id=ch.tvg_id,
                    chno=None,
                )
            )

        return self._finish(target, kept, total_input, epg)

    def _match_cache_for(
        self, index: int | str, epg: EPGData, overrides: dict[str, str],
    ) -> dict[tuple, tuple[str, str]]:
        """Cache de emparejamiento por canal, invalidada por guía u overrides.

        La clave incluye ``tvg_id`` y nombre, así que si la lista cambia el
        canal se vuelve a emparejar solo. El valor guarda el id de guía y la
        identidad canónica: las dos se resuelven igual de caras.
        """
        signature = (id(epg), frozenset(overrides.items()))
        cached = self._match_cache.get(index)
        if cached is None or cached[0] != signature:
            cached = (signature, {})
            self._match_cache[index] = cached
        return cached[1]

    @staticmethod
    def _resolver(cache: dict[tuple, tuple[str, str]], matcher: EpgMatcher,
                  mapped: MappedChannel, override: str | None = None,
                  ) -> tuple[str | None, str | None]:
        """Id de guía y identidad canónica de un canal, con cache.

        Son dos preguntas distintas: ``matched_epg_id`` decide de qué guía
        salen los programas (un mapeo manual manda sobre esto) y
        ``canonical_id`` dice de qué canal estamos hablando (dial, logo y
        agrupación de variantes). Antes las dos se contestaban con el mismo
        id, y por eso un mapeo a Koala dejaba el canal sin dial.

        La cache se invalida cuando cambian los mapeos (la firma los incluye),
        así que guardar el id del mapeo dentro no la deja obsoleta.
        """
        clave = (mapped.url, mapped.tvg_id or "", mapped.name)
        guardado = cache.get(clave)
        if guardado is None:
            epg_id = override or matcher.match(mapped.tvg_id, mapped.name)
            canonico = matcher.canonical_for(mapped.name, epg_id)
            guardado = (epg_id or "", canonico or "")
            cache[clave] = guardado
        return guardado[0] or None, guardado[1] or None

    def _finish(self, target: PipelineTarget, kept: list[MappedChannel],
                total_input: int, epg: EPGData | None) -> PipelineResult:
        """Numeración secuencial + match EPG, común a ambos flujos."""
        # Canales desactivados manualmente: excluidos antes de numerar.
        disabled = set(target.disabled_channels)
        excluded: list[MappedChannel] = []
        if disabled:
            excluded = [m for m in kept if m.url in disabled]
            kept = [m for m in kept if m.url not in disabled]
        # La numeración va al final, sobre los canales que realmente se
        # sirven: ver el bloque de la regla de servidor.

        # Match EPG (con cache: emparejar no depende de las reglas)
        matched: list[MappedChannel] = []
        if epg is not None:
            matcher = EpgMatcher(epg)
            cache = self._match_cache_for(target.source_index, epg,
                                          target.epg_overrides)
            for mapped in kept:
                # un mapeo manual fija la parrilla y no gasta emparejamiento
                override = target.epg_overrides.get(mapped.url)
                epg_id, canonico = self._resolver(
                    cache, matcher, mapped, override)
                perfil = profile_of(target, canonico, mapped.url, mapped.name)
                # Sin guía: el dial forzado (o el toggle del target) la
                # resuelven con la guía de relleno, antes de que la regla de
                # servidor descarte el canal.
                if not epg_id and (perfil.dial is not None
                                   or target.serve_without_epg):
                    epg_id = dummy_id_for(
                        channel_identity(canonico, mapped.url, mapped.name))
                matched.append(dataclasses_replace(
                    mapped, matched_epg_id=epg_id, canonical_id=canonico))
        else:
            matched = kept

        # Regla de servidor: un canal sin guía asignada no se sirve.
        #
        # Orden: match -> descarte -> numeración. Numerar antes de descartar
        # dejaría los `chno` con huecos (1, 3, 4...) en la lista que ve el
        # reproductor.
        #
        # El Dummy cuenta como guía asignada: es la vía de rescate que la UI
        # ofrece para un canal sin EPG real, y si no colara aquí, asignarlo no
        # serviría de nada.
        #
        # ``require_epg`` es derivado, no una opción del usuario: se exige guía
        # solo cuando hay una guía que exigir. Sin fuente EPG la lista dejaría
        # de funcionar por un motivo que no es culpa del canal.
        #
        # Conocido y aceptado por el usuario (2026-10-14): con una EPG que
        # empareja mal, esta regla vacía la lista. El recorte se informa en el
        # log y ``excluded_no_epg`` alimenta la tabla para poder rescatar.
        require_epg = epg is not None
        no_epg: list[MappedChannel] = []
        if require_epg:
            served: list[MappedChannel] = []
            for mapped in matched:
                if mapped.matched_epg_id:
                    served.append(mapped)
                else:
                    no_epg.append(mapped)
        else:
            served = matched
        # Variantes de calidad: de cada canal (mismo id EPG y mismo nombre
        # base) se sirve una sola. Va después del apagado manual, así que
        # desactivar el 4K a mano deja pasar el HD.
        served, duplicates = dedupe_variants(served, target.quality_priority)
        # ``unmatched`` es siempre el diagnóstico de la EPG: con la regla
        # activada son los descartados, no los servidos sin guía.
        unmatched = [m.name for m in (no_epg if require_epg else served)]
        numbered = self._finalize(served, target, epg)

        logger.info(
            "Pipeline '%s': %d canales entrada -> %d tras filtros, "
            "%d sin guía EPG%s",
            target.name,
            total_input,
            len(numbered),
            len(unmatched),
            f", {len(no_epg)} descartados" if require_epg else " (sin fuente EPG)",
        )
        if duplicates:
            logger.info(
                "Pipeline '%s': %d variantes descartadas por prioridad de "
                "calidad (se sirve la mejor de cada canal)", target.name,
                len(duplicates))
        return PipelineResult(numbered, total_input, unmatched, excluded,
                              no_epg, require_epg, duplicates)

    def _finalize(self, served: list[MappedChannel], target: PipelineTarget,
                  epg: EPGData | None) -> list[MappedChannel]:
        """Pone logo y número final: dial real si la guía lo publica.

        - **Logo** por capas: el de la guía del canal canónico (el que el
          proveedor usa ahora), el de la guía del emparejamiento y, si no lo
          publica ninguna, el que traía la lista M3U.
        - **Número**: ``tvg-chno`` sale del ``dial`` del canal cuando existe
          (1, 2, 3, 4, 7…, con los huecos de la parrilla real) y de un
          contador que empieza **después del último dial** para los que no
          tienen número propio. Meterlos en los huecos intermedios dejaría un
          local en el 7 de «Movistar Plus», y el dial dejaría de parecerse a
          la realidad.

        Antes de este cambio la numeración era la posición en la lista; ahora
        es el dial, y la lista sale ordenada por él para que el reproductor
        muestre el mismo orden que la parrilla del proveedor.
        """
        info = epg.channel_info if epg is not None else {}
        nombres = epg.channel_names if epg is not None else {}
        # Un mapeo manual puede apuntar a una guía que no publica dial ni logo
        # (la de siempre): el canal canónico cubre ese hueco y, si tampoco hay
        # identidad, se busca por nombre.
        por_nombre = self._meta_por_nombre(epg) if epg is not None else {}
        con_dial: list[MappedChannel] = []
        sin_dial: list[MappedChannel] = []
        usados: set[int] = set()
        for mapped in served:
            perfil = profile_of(target, mapped.canonical_id, mapped.url,
                                mapped.name)
            # La identidad manda: el dial y el logo son del canal, no de la
            # guía que resolvió la parrilla. Los respaldos son para canales
            # sin identidad reconocida.
            meta = _combinar(
                info.get(mapped.canonical_id or ""),
                _combinar(info.get(mapped.matched_epg_id or ""),
                          por_nombre.get(base_name(mapped.name))))
            logo = (meta.logo if meta is not None else None) or mapped.logo_url
            # El nombre mostrado es cosmético: la guía del canal canónico pone
            # el nombre real ("LA 1" para "LA 1 HD") y la ficha lo puede
            # sobrescribir. El dedupe ya se hizo con el nombre interno.
            # Si la guía solo publica el código como nombre ("LA2"), no se usa:
            # sería peor que el que trae la lista.
            de_guia = nombres.get(mapped.canonical_id or "") or ""
            if de_guia == (mapped.canonical_id or ""):
                de_guia = ""
            nombre = perfil.display or de_guia or mapped.name
            canal = dataclasses_replace(
                mapped, logo_url=logo, logo_file=perfil.logo or None,
                name=nombre,
                internal_name=mapped.internal_name or mapped.name)
            dial = perfil.dial if perfil.dial is not None else (
                meta.dial if meta is not None else None)
            # Un dial repetido (dos canales apuntando al mismo id de guía) no
            # puede duplicar el número: el segundo cae al contador.
            if dial is not None and dial not in usados:
                usados.add(dial)
                con_dial.append(dataclasses_replace(canal, chno=dial))
            else:
                sin_dial.append(canal)
        # Sin dial propio, el canal va después del último dial: los números
        # bajos son de canales reales y no se reutilizan.
        siguiente = (max(max(usados) + 1, target.starting_number) if usados
                     else target.starting_number)
        for canal in sin_dial:
            while siguiente in usados:
                siguiente += 1
            usados.add(siguiente)
            con_dial.append(dataclasses_replace(canal, chno=siguiente))
            siguiente += 1
        return sorted(con_dial, key=lambda m: (m.chno or 0, m.name))

    @staticmethod
    def _meta_por_nombre(epg: EPGData) -> dict[str, ChannelInfo]:
        """Canal (por nombre base) → dial/logo, venga de la guía que venga.

        Es el respaldo cuando el id que resolvió el emparejamiento —o el
        mapeo manual— pertenece a una guía que no publica metadatos. Se leen de
        mayor a menor prioridad y cada campo se completa con el primero que lo
        traiga: el dial puede venir de Movistar y el logo de otra guía.
        """
        elegido: dict[str, ChannelInfo] = {}
        for cid in sorted(epg.channel_info,
                          key=lambda c: -epg.channel_priority.get(c, 0)):
            meta = epg.channel_info[cid]
            clave = base_name(epg.channel_names.get(cid, ""))
            if not clave:
                continue
            actual = elegido.get(clave) or ChannelInfo()
            elegido[clave] = ChannelInfo(
                dial=actual.dial if actual.dial is not None else meta.dial,
                logo=actual.logo or meta.logo)
        return elegido
