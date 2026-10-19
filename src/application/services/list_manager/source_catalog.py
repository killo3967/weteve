"""Cartera global de fuentes (listas IPTV y guías EPG).

Las fuentes dejan de pertenecer a un target: se definen una vez en la
cartera (sección [SOURCES] de config.ini) y cada target selecciona qué
filas usar. La lista servida es la unión de las filas IPTV activas y
la guía, la unión de las filas EPG activas.

Qt-free, sin I/O: la persistencia vive en src.infrastructure.client.
"""

import uuid
from dataclasses import dataclass, replace
from typing import Literal

SourceKind = Literal["m3u", "epg"]
# "movistar" no es una URL ni un fichero: es la API OTT de Movistar+, que
# publica dial (``Dial``) y logo además de la parrilla. El esquema de la
# ubicación (``movistar://epg?span=7``) es lo que la distingue.
SourceType = Literal["file", "url", "movistar"]


def infer_source_type(location: str) -> SourceType:
    """Tipo de origen deducido de la ubicación (única fuente de verdad).

    La usan la validación de la API, la escritura de la cartera y la UI, para
    que un mismo texto no se clasifique distinto según por dónde entre.
    """
    loc = str(location or "").strip().lower()
    if loc.startswith("movistar:"):
        return "movistar"
    if loc.startswith(("http://", "https://")):
        return "url"
    return "file"


def new_source_id() -> str:
    """Identificador corto y estable de una entrada de la cartera."""
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class SourceEntry:
    """Una lista (IPTV o EPG) de la cartera global.

    Attributes:
        id: identificador estable (no cambia al renombrar ni reordenar).
        name: nombre visible de la lista; se usa como prefijo de canal
            ("Lista.Canal") al renderizar la playlist.
        kind: "m3u" para listas de canales, "epg" para guías.
        source_type: "file" (ruta local) o "url" (descarga HTTP).
        location: ruta o URL de la lista.
        refresh_min: minutos entre refreescos automáticos; 0 = estática
            (solo se recarga manualmente).
        enabled: si la fila participa en la unión.
    """

    id: str
    name: str
    kind: SourceKind
    source_type: SourceType
    location: str
    refresh_min: int = 0
    enabled: bool = True

    def with_updates(self, **changes) -> "SourceEntry":
        return replace(self, **changes)


class SourceCatalog:
    """Colección ordenada de SourceEntry con búsqueda por id."""

    def __init__(self, entries: list[SourceEntry] | None = None) -> None:
        self._entries: dict[str, SourceEntry] = {}
        for e in entries or []:
            self._entries[e.id] = e

    # --- lectura -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._entries)

    def get(self, source_id: str) -> SourceEntry | None:
        return self._entries.get(source_id)

    def all(self) -> list[SourceEntry]:
        return list(self._entries.values())

    def of_kind(self, kind: SourceKind, enabled_only: bool = False) -> list[SourceEntry]:
        out = [e for e in self._entries.values() if e.kind == kind]
        if enabled_only:
            out = [e for e in out if e.enabled]
        return out

    def min_refresh_min(self, ids: tuple[str, ...] | list[str]) -> int:
        """Refresco más corto (en minutos) entre las ids dadas.

        Devuelve 0 si ninguna de las entradas activas tiene refresco
        positivo: los targets estáticos no arrancan planificador.
        """
        periods = [
            e.refresh_min for e in (self.get(i) for i in ids)
            if e is not None and e.enabled and e.refresh_min > 0
        ]
        return min(periods) if periods else 0

    # --- mutación ----------------------------------------------------------
    def add(self, entry: SourceEntry) -> SourceEntry:
        """Inserta (o reemplaza por id) una entrada. Devuelve la guardada."""
        self._entries[entry.id] = entry
        return entry

    def add_new(self, name: str, kind: SourceKind, source_type: SourceType,
                location: str, refresh_min: int = 0,
                enabled: bool = True) -> SourceEntry:
        return self.add(SourceEntry(
            id=new_source_id(), name=name, kind=kind,
            source_type=source_type, location=location,
            refresh_min=refresh_min, enabled=enabled,
        ))

    def remove(self, source_id: str) -> bool:
        return self._entries.pop(source_id, None) is not None

    def update(self, source_id: str, **changes) -> SourceEntry | None:
        entry = self._entries.get(source_id)
        if entry is None:
            return None
        changes = {k: v for k, v in changes.items()
                   if k in {"name", "kind", "source_type", "location",
                            "refresh_min", "enabled"}}
        updated = entry.with_updates(**changes)
        self._entries[source_id] = updated
        return updated
