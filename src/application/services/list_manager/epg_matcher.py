"""Asociación M3U <-> EPG (XMLTV) del gestor de listas.

Match automático en dos fases (especificación: odd/tasks/list-manager.md):
  1. ``tvg-id`` exacto del canal contra los channel-ids del XMLTV.
  2. Fallback por nombre normalizado (sin acentos, lowercase, sin puntuación).
Sin match: el canal queda sin EPG y el editor manual puede asignarlo mediante
``epg_overrides`` (persistidos con prioridad sobre el automático).
"""

import difflib
import unicodedata

from src.domain.entities.epg import EPGData


def normalize_channel_name(name: str) -> str:
    """Normalización de nombres para matching (NFKD + sin diacríticos + lowercase).

    Más tolerante que ``normalize_name`` de ``epg.py``: conserva espacios
    colapsados para permitir comparaciones legibles y sugerencias.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = without_accents.lower()
    collapsed = " ".join(lowered.split())
    return collapsed


class EpgMatcher:
    """Asocia canales M3U con channel-ids de una EPG XMLTV."""

    def __init__(self, epg: EPGData):
        self._epg = epg
        # Normaliza los channel-ids del XMLTV para match por tvg-id
        # case-insensitive.
        self._ids_by_normalized_id: dict[str, str] = {}
        for channel_id in epg.programs_by_channel_ids():
            norm = channel_id.strip().lower()
            if norm and norm not in self._ids_by_normalized_id:
                self._ids_by_normalized_id[norm] = channel_id
        # nombre normalizado -> channel-id (primer id que llegue)
        self._id_by_normalized_name: dict[str, str] = {}
        for channel_id, display_name in epg.channel_names.items():
            norm_name = normalize_channel_name(display_name)
            if norm_name and norm_name not in self._id_by_normalized_name:
                self._id_by_normalized_name[norm_name] = channel_id

    def match(self, tvg_id: str | None, name: str) -> str | None:
        """Devuelve el channel-id del XMLTV para un canal, o None."""
        if tvg_id:
            norm_id = tvg_id.strip().lower()
            if norm_id in self._ids_by_normalized_id:
                return self._ids_by_normalized_id[norm_id]
        norm_name = normalize_channel_name(name)
        return self._id_by_normalized_name.get(norm_name)

    def suggestions(self, name: str, limit: int = 5) -> list[tuple[str, str]]:
        """Hasta ``limit`` sugerencias (channel-id, nombre) por similitud."""
        norm_name = normalize_channel_name(name)
        if not norm_name:
            return []
        scored: list[tuple[float, str, str]] = []
        for channel_id, display_name in self._epg.channel_names.items():
            candidate = normalize_channel_name(display_name)
            ratio = difflib.SequenceMatcher(None, norm_name, candidate).ratio()
            scored.append((ratio, channel_id, display_name))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [(cid, display) for ratio, cid, display in scored[:limit] if ratio > 0.3]
