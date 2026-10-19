from abc import ABC, abstractmethod

from ..entities.channel import Channel


class IPlaylistRepository(ABC):
    """Puerto de salida (Driven Port) para el acceso a datos de listas M3U."""

    @abstractmethod
    def get_channels(self, source: str, fresh: bool = False) -> list[Channel]:
        """Recupera la lista de canales desde una fuente (archivo o URL).

        ``fresh=True`` fuerza volver a leer la fuente (ignora la cache).
        """
