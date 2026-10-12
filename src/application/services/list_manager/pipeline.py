"""Pipeline del gestor de listas: parse -> filtros -> match EPG -> salida."""

import logging
from dataclasses import replace as dataclasses_replace

from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import (
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
    ):
        self.channels = channels
        self.total_input = total_input
        self.unmatched = unmatched  # nombres de canales sin EPG

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
        # cache de canales por fuente para get_groups (evita re-descargar)
        self._channel_cache: dict[int | str, list] = {}

    def register_source(self, index: int | str, m3u_path: str, epg_path: str | None) -> None:
        """Registra (sin descargar) la fuente M3U y su EPG opcional."""
        self._sources[index] = (m3u_path, None)
        if epg_path:
            epg = self._epg_repo.load_epg(epg_path)
            self._sources[index] = (m3u_path, epg)

    def register_merged(self, index: int | str, source_refs: tuple,
                        epg_refs: tuple) -> None:
        """Registra un target multi-fuente (cartera global de listas).

        ``source_refs``/``epg_refs`` son tuplas de SourceRef (id, name,
        location). Los canales no se descargan hasta el primer uso; la EPG
        se fusiona en un solo EPGData (unión por id de canal).
        """
        self._merged[index] = (tuple(source_refs), tuple(epg_refs))
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

    def _load_channels(self, source: int | str | None) -> list:
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

    def get_epg(self, index: int | str) -> EPGData | None:
        """Devuelve la EPG cacheada de una fuente registrada, o None."""
        entry = self._sources.get(index)
        return entry[1] if entry else None

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
        raw_channels = self._load_channels(target.source_index)
        total_input = len(raw_channels)

        epg = self._merge_epgs(epg_refs)
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

    def _merge_epgs(self, epg_refs: tuple) -> EPGData | None:
        """Unión de las guías activas: programas suman y nombres se completan.

        Debe construirse un único EPGData al final: el agregado indexa los
        programas por canal/nombre solo en el constructor.
        """
        if not epg_refs:
            return None
        programs: list[Program] = []
        names: dict[str, str] = {}
        for ref in epg_refs:
            epg = self._epg_repo.load_epg(ref.location)
            if epg is None:
                continue
            programs.extend(epg.programs)
            for cid, name in epg.channel_names.items():
                if name and not names.get(cid):
                    names[cid] = name
        if not programs and not names:
            return None
        return EPGData(programs=programs, channel_names=names)

    def _run_single(self, target: PipelineTarget, m3u_path: str,
                    epg: EPGData | None) -> PipelineResult:
        """Flujo antiguo de fuente única (sin cartera)."""

        raw_channels = self._m3u_repo.get_channels(m3u_path)
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
        numbered: list[MappedChannel] = []
        for i, mapped in enumerate(kept, start=target.starting_number):
            numbered.append(dataclasses_replace(mapped, chno=i))
        kept = numbered

        # Match EPG
        unmatched: list[str] = []
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
                if not epg_id:
                    unmatched.append(mapped.name)
        else:
            matched = kept
            unmatched = [m.name for m in kept]
        kept = matched

        logger.info(
            "Pipeline '%s': %d canales entrada -> %d tras filtros, %d sin EPG",
            target.name,
            total_input,
            len(kept),
            len(unmatched),
        )
        return PipelineResult(kept, total_input, unmatched)
