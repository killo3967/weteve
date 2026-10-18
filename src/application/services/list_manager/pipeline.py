"""Pipeline del gestor de listas: parse -> filtros -> match EPG -> salida."""

import logging
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import (
    FilterRule,
    MappedChannel,
    PipelineTarget,
)
from src.domain.entities.epg import EPGData, Program
from src.infrastructure.adapters.file_m3u_repository import FileM3URepository
from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

logger = logging.getLogger(__name__)


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
        epg_repository: XMLTVRepository | None = None,
    ):
        self._m3u_repo = m3u_repository or FileM3URepository()
        self._epg_repo = epg_repository or XMLTVRepository()
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

    def register_source(self, index: int | str, m3u_path: str, epg_path: str | None) -> None:
        """Registra (sin descargar) la fuente M3U y su EPG opcional."""
        # registrar invalida la cache de canales: el fichero/URL puede haber
        # cambiado desde la última lectura y la vista previa debe ver lo mismo
        # que el pipeline completo.
        self._channel_cache.pop(index, None)
        self._sources[index] = (m3u_path, None)
        self._epg_lists.pop(index, None)
        if epg_path:
            epg = self._epg_repo.load_epg(epg_path)
            self._sources[index] = (m3u_path, epg)
            if epg is not None:
                label = Path(str(epg_path)).name or str(epg_path)
                self._epg_lists[index] = {cid: (label,)
                                          for cid in epg.channel_names}

    def register_merged(self, index: int | str, source_refs: tuple,
                        epg_refs: tuple) -> None:
        """Registra un target multi-fuente (cartera global de listas).

        ``source_refs``/``epg_refs`` son tuplas de SourceRef (id, name,
        location). Los canales no se descargan hasta el primer uso; la EPG
        se fusiona en un solo EPGData (unión por id de canal).
        """
        self._channel_cache.pop(index, None)
        self._merged[index] = (tuple(source_refs), tuple(epg_refs))
        # la EPG fusionada se calcula aquí (register_source también carga la
        # suya de forma anticipada): run() y get_epg() la leen de la cache
        self._merged_epg[index], self._epg_lists[index] = self._merge_epgs(
            self._merged[index][1])
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

    def _merge_epgs(self, epg_refs: tuple
                    ) -> tuple[EPGData | None, dict[str, tuple[str, ...]]]:
        """Unión de las guías activas: programas suman y nombres se completan.

        Debe construirse un único EPGData al final: el agregado indexa los
        programas por canal/nombre solo en el constructor.

        Devuelve además, por channel-id, las guías que lo anuncian (el nombre
        de la fuente, no su ruta): la tabla de mapeo lo muestra y así dos
        fuentes con el mismo nombre de canal dejan de ser indistinguibles.
        """
        if not epg_refs:
            return None, {}
        programs: list[Program] = []
        names: dict[str, str] = {}
        origins: dict[str, list[str]] = {}
        for ref in epg_refs:
            epg = self._epg_repo.load_epg(ref.location)
            if epg is None:
                continue
            label = str(getattr(ref, "name", "") or "") or Path(
                str(getattr(ref, "location", ""))).name
            programs.extend(epg.programs)
            for cid, name in epg.channel_names.items():
                if name and not names.get(cid):
                    names[cid] = name
                if label and label not in origins.setdefault(cid, []):
                    origins[cid].append(label)
        lists = {cid: tuple(labels) for cid, labels in origins.items()}
        if not programs and not names:
            return None, lists
        return EPGData(programs=programs, channel_names=names), lists

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

        # Match EPG
        matched: list[MappedChannel] = []
        if epg is not None:
            matcher = EpgMatcher(epg)
            for mapped in kept:
                override = target.epg_overrides.get(mapped.url)
                if override:
                    matched.append(dataclasses_replace(mapped, matched_epg_id=override))
                    continue
                epg_id = matcher.match(mapped.tvg_id, mapped.name)
                matched.append(dataclasses_replace(mapped, matched_epg_id=epg_id))
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
        # ``unmatched`` es siempre el diagnóstico de la EPG: con la regla
        # activada son los descartados, no los servidos sin guía.
        unmatched = [m.name for m in (no_epg if require_epg else served)]
        numbered = [
            dataclasses_replace(m, chno=i)
            for i, m in enumerate(served, start=target.starting_number)
        ]

        logger.info(
            "Pipeline '%s': %d canales entrada -> %d tras filtros, "
            "%d sin guía EPG%s",
            target.name,
            total_input,
            len(numbered),
            len(unmatched),
            f", {len(no_epg)} descartados" if require_epg else " (sin fuente EPG)",
        )
        return PipelineResult(numbered, total_input, unmatched, excluded,
                              no_epg, require_epg)
