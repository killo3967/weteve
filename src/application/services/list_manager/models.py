"""Modelos de dominio del gestor de listas.

Qt-free, sin I/O. Los patrones de filtro persisten en sintaxis compatible con
xTeVe (ver odd/tasks/list-manager.md, sección "Filtros").
"""

from dataclasses import dataclass, field
from typing import Literal

FilterField = Literal["group-title", "custom-filter"]


@dataclass(frozen=True)
class FilterRule:
    """Una regla de filtro sobre los canales de una fuente.

    ``pattern`` usa la semántica xTeVe:
      - texto plano: coincide si aparece en el campo objetivo.
      - ``{texto}``: incluir además los canales que cumplan ``texto``.
      - ``!{texto}``: excluir los canales que cumplan ``texto``.
    """

    field: FilterField
    pattern: str
    exclude: bool = False
    case_sensitive: bool = False

    def serialize(self) -> str:
        """Formato plano para config.ini: ``field:pattern:case`` (case 0/1)."""
        return f"{self.field}:{self.pattern}:{int(self.case_sensitive)}"

    @staticmethod
    def parse(raw: str) -> "FilterRule":
        """Parsea ``field:pattern:case``. Detecta ``!{...}`` como exclusión.

        Tolerante con formatos parciales (sin campo, sin case).
        """
        import re

        parts = raw.split(":", 2)
        field: FilterField = "custom-filter"
        pattern = raw
        case_sensitive = False
        if len(parts) >= 2 and parts[0] in ("group-title", "custom-filter"):
            field = parts[0]  # type: ignore[assignment]
            pattern = parts[1]
            if len(parts) == 3:
                case_sensitive = parts[2] == "1"
        exclude = bool(re.search(r"!+\{[^.]+\}", pattern))
        return FilterRule(field=field, pattern=pattern, exclude=exclude,
                          case_sensitive=case_sensitive)


@dataclass(frozen=True)
class PipelineTarget:
    """Un objetivo de pipeline: fuente + filtros + EPG + servicio."""

    name: str
    source_index: int
    rules: tuple[FilterRule, ...] = ()
    epg_source_index: int | None = None
    # canal (clave estable, p. ej. url) -> tvg-id del XMLTV (override manual)
    epg_overrides: dict[str, str] = field(default_factory=dict)
    port: int = 8080
    starting_number: int = 1
    enabled: bool = True


@dataclass(frozen=True)
class ServiceConfig:
    """Configuración global del servicio web del gestor."""

    enabled: bool = False
    port: int = 8080
    bind: str = "127.0.0.1"


@dataclass(frozen=True)
class MappedChannel:
    """Canal resultante del pipeline, listo para exportar."""

    name: str
    url: str
    group: str = ""
    logo_url: str | None = None
    tvg_id: str | None = None
    tvg_name: str | None = None
    chno: int | None = None
    # tvg-id asignado tras el matching (None si el canal queda sin EPG)
    matched_epg_id: str | None = None
