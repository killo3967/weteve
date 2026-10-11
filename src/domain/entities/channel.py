from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    """Entidad que representa un canal de IPTV."""
    name: str
    url: str
    group: str = "Otros"
    logo_url: str | None = None
    tvg_id: str | None = None

    def __post_init__(self):
        if not self.name:
            raise ValueError("El nombre del canal no puede estar vacío")
        if not self.url:
            raise ValueError("La URL del canal no puede estar vacía")
