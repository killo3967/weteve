"""Pipeline del gestor de listas: parse -> filtros -> match EPG -> salida."""

import logging
from dataclasses import replace as dataclasses_replace

from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.filter_engine import evaluate_channel
from src.application.services.list_manager.models import (
    MappedChannel,
    PipelineTarget,
)
from src.domain.entities.epg import EPGData
from src.infrastructure.adapters.file_m3u_repository import FileM3URepository
from src.infrastructure.adapters.xmltv_repository import XMLTVRepository

logger = logging.getLogger(__name__)


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

    def register_source(self, index: int | str, m3u_path: str, epg_path: str | None) -> None:
        """Registra (sin descargar) la fuente M3U y su EPG opcional."""
        self._sources[index] = (m3u_path, None)
        if epg_path:
            epg = self._epg_repo.load_epg(epg_path)
            self._sources[index] = (m3u_path, epg)

    def get_epg(self, index: int | str) -> EPGData | None:
        """Devuelve la EPG cacheada de una fuente registrada, o None."""
        entry = self._sources.get(index)
        return entry[1] if entry else None

    def run(self, target: PipelineTarget) -> PipelineResult:
        """Ejecuta el pipeline completo sobre el target."""
        if target.source_index not in self._sources:
            raise ValueError(f"Fuente {target.source_index} no registrada")
        m3u_path, epg = self._sources[target.source_index]

        raw_channels = self._m3u_repo.get_channels(m3u_path)
        total_input = len(raw_channels)

        kept: list[MappedChannel] = []
        for ch in raw_channels:
            rules = list(target.rules)
            if not evaluate_channel(ch.name, ch.group, ch.url, rules):
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

        # Asignación de número de canal secuencial
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
