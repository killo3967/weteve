from abc import ABC, abstractmethod

from ..entities.epg import EPGData


class IEPGRepository(ABC):
    """Puerto de salida (Driven Port) para la recuperación de datos EPG (XMLTV)."""
    
    @abstractmethod
    def load_epg(self, source: str) -> EPGData:
        """Descarga y parsea una fuente XMLTV para generar datos EPG."""
