from abc import ABC, abstractmethod

from ..entities.epg import EPGData


class IEPGRepository(ABC):
    """Puerto de salida (Driven Port) para la recuperación de datos EPG (XMLTV)."""

    @abstractmethod
    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        """Descarga y parsea una fuente XMLTV para generar datos EPG.

        ``fresh=True`` fuerza volver a leer la fuente (ignora cualquier
        cache); por defecto se permite reutilizar lo ya cargado.
        """
