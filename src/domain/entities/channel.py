from dataclasses import dataclass, field


@dataclass(frozen=True)
class Channel:
    """Entidad que representa un canal de IPTV.

    ``groups`` agrupa los campos de agrupación que trae la línea EXTINF:
    nombre de campo -> lista de grupos (un canal puede tener varios).
    Claves posibles: "group-title", "tvg-group", "extgrp".
    ``group`` sigue siendo el grupo primario (primer group-title u "Otros")
    para compatibilidad con EPG y salida M3U.
    """

    name: str
    url: str
    group: str = "Otros"
    logo_url: str | None = None
    tvg_id: str | None = None
    groups: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name:
            raise ValueError("El nombre del canal no puede estar vacío")
        if not self.url:
            raise ValueError("La URL del canal no puede estar vacía")
