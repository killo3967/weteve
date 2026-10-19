"""Router de fuentes EPG: elige el adaptador según el esquema de la ubicación.

La cartera guarda una única ``location`` por fuente de guía, y esa cadena es
la que decide quién la lee: ``movistar://...`` va a la API de Movistar+ y
todo lo demás (``http(s)://`` o una ruta local) al lector XMLTV de siempre.
El pipeline no necesita saberlo: pide ``load_epg`` y recibe ``EPGData``.
"""

from src.domain.entities.epg import EPGData
from src.domain.ports.i_epg_repo import IEPGRepository
from src.infrastructure.adapters.movistar_epg_repository import (
    MovistarEPGRepository,
)
from src.infrastructure.adapters.xmltv_repository import XMLTVRepository


class EPGRepositoryRouter(IEPGRepository):
    """Reparte ``load_epg`` entre los adaptadores disponibles."""

    def __init__(self, routes: list[tuple[str, IEPGRepository]] | None = None,
                 default: IEPGRepository | None = None) -> None:
        self._routes = list(routes) if routes is not None else [
            ("movistar:", MovistarEPGRepository()),
        ]
        self._default = default if default is not None else XMLTVRepository()

    def repository_for(self, source: str) -> IEPGRepository:
        """Adaptador que atiende ``source`` (útil para diagnóstico y tests)."""
        loc = str(source or "").strip().lower()
        for prefix, repo in self._routes:
            if loc.startswith(prefix):
                return repo
        return self._default

    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        return self.repository_for(source).load_epg(source, fresh=fresh)
