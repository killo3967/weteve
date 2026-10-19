"""Asociación M3U <-> EPG (XMLTV) del gestor de listas.

Match automático en tres fases (especificación: odd/tasks/list-manager.md):
  1. ``tvg-id`` exacto del canal contra los channel-ids del XMLTV.
  2. Fallback por nombre normalizado (sin acentos, lowercase, sin puntuación).
  3. Fallback por nombre base, sin la etiqueta de calidad: el proveedor lista
     ``LA 1 HD``/``LA 1 FHD`` y la guía los anuncia como ``LA 1``.
Sin match: el canal queda sin EPG y el editor manual puede asignarlo mediante
``epg_overrides`` (persistidos con prioridad sobre el automático).
"""

import difflib
import unicodedata

from src.application.services.list_manager.quality import base_name
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
        # Ids de la guía que publica dial/logo (Movistar+): cuando dos guías
        # anuncian el mismo canal, gana la que aporta los metadatos, porque es
        # la única que puede dar número real y logo.
        self._con_metadatos = set(epg.channel_info)
        # Peso de la guía que anuncia cada id: es el desempate principal entre
        # candidatos de guías distintas (lo fija la cartera, no el canal).
        self._prioridades = epg.channel_priority
        # Normaliza los channel-ids del XMLTV para match por tvg-id
        # case-insensitive.
        self._ids_by_normalized_id: dict[str, str] = {}
        for channel_id in epg.programs_by_channel_ids():
            norm = channel_id.strip().lower()
            if norm and norm not in self._ids_by_normalized_id:
                self._ids_by_normalized_id[norm] = channel_id
        # nombre normalizado -> channel-id, y nombre sin la etiqueta de
        # calidad -> channel-id (el proveedor lista "LA 1 HD"/"LA 1 FHD" y la
        # guía los anuncia como "LA 1").
        self._id_by_normalized_name: dict[str, str] = {}
        self._id_by_base_name: dict[str, str] = {}
        for channel_id, display_name in epg.channel_names.items():
            norm_name = normalize_channel_name(display_name)
            if norm_name:
                self._preferir(self._id_by_normalized_name, norm_name, channel_id)
            base = base_name(display_name)
            if base:
                self._preferir(self._id_by_base_name, base, channel_id)

    def _preferir(self, mapa: dict[str, str], clave: str, channel_id: str) -> None:
        """Guarda el id para ``clave``; gana la guía de más prioridad.

        A igual prioridad gana la que publica dial y logo (es la única que
        puede dar número real); sin ella, el primero que llega, que es el
        orden de la cartera.
        """
        previo = mapa.get(clave)
        if previo is None:
            mapa[clave] = channel_id
            return
        nueva = self._prioridades.get(channel_id, 0)
        actual = self._prioridades.get(previo, 0)
        if nueva > actual:
            mapa[clave] = channel_id
        elif nueva == actual and (channel_id in self._con_metadatos
                                  and previo not in self._con_metadatos):
            mapa[clave] = channel_id

    def match(self, tvg_id: str | None, name: str) -> str | None:
        """Devuelve el channel-id del XMLTV para un canal, o None."""
        if tvg_id:
            norm_id = tvg_id.strip().lower()
            if norm_id in self._ids_by_normalized_id:
                return self._ids_by_normalized_id[norm_id]
        norm_name = normalize_channel_name(name)
        base = base_name(name)
        candidatos: list[tuple[str, bool]] = []
        exacto = self._id_by_normalized_name.get(norm_name)
        if exacto:
            candidatos.append((exacto, True))
        por_base = self._id_by_base_name.get(base) if base else None
        if por_base and por_base != exacto:
            candidatos.append((por_base, False))
        if not candidatos:
            return None

        def peso(candidato: tuple[str, bool]) -> tuple[int, bool, bool]:
            cid, es_exacto = candidato
            # prioridad de la guía > guía que publica dial/logo > nombre exacto
            return (self._prioridades.get(cid, 0), cid in self._con_metadatos,
                    es_exacto)

        return max(candidatos, key=peso)[0]

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
