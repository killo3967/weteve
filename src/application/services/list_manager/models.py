"""Modelos de dominio del gestor de listas.

Qt-free, sin I/O. Los patrones de filtro persisten en sintaxis compatible con
xTeVe (ver odd/tasks/list-manager.md, sección "Filtros").
"""

from dataclasses import dataclass, field
from typing import Literal

# Campos fijos conocidos; los dinámicos (tvg-group, extgrp, futuros) son
# cualesquiera claves presentes en Channel.groups.
FilterField = Literal["group-title", "custom-filter", "tvg-group", "extgrp", "source"]
RuleOperator = Literal["or", "and"]


@dataclass(frozen=True)
class FilterRule:
    """Una regla de filtro sobre los canales de una fuente.

    ``pattern`` usa la semántica xTeVe:
      - texto plano: coincide si aparece en el campo objetivo.
      - ``{texto}``: incluir además los canales que cumplan ``texto``.
      - ``!{texto}``: excluir los canales que cumplan ``texto``.

    ``operator`` es cómo se combina esta regla con las anteriores:
      - "or" (por defecto, compatibilidad con configs xTeVe): el canal pasa si
        alguna regla OR coincide.
      - "and": esta regla debe cumplir además de las anteriores.
    """

    field: FilterField
    pattern: str
    exclude: bool = False
    case_sensitive: bool = False
    operator: RuleOperator = "or"

    def serialize(self) -> str:
        """Formato plano para config.ini: ``field:pattern:case[:op]``.

        El operador solo se escribe si no es el default (back-compat con
        configs xTeVe existentes).
        """
        base = f"{self.field}:{self.pattern}:{int(self.case_sensitive)}"
        if self.operator != "or":
            base += f":{self.operator}"
        return base

    @staticmethod
    def parse(raw: str) -> "FilterRule":
        """Parsea ``field:pattern:case[:op]``. Detecta ``!{...}`` como exclusión.

        Tolerante con formatos parciales (sin campo, sin case, sin operador).
        """
        import re

        parts = raw.split(":")
        f: FilterField = "custom-filter"
        pattern = raw
        case_sensitive = False
        operator: RuleOperator = "or"
        if len(parts) >= 2 and parts[0] in (
            "group-title", "custom-filter", "tvg-group", "extgrp", "source"
        ):
            f = parts[0]  # type: ignore[assignment]
            if len(parts) >= 3:
                tail = parts[-1]
                if tail in ("and", "or"):
                    operator = tail  # type: ignore[assignment]
                    case_sensitive = parts[-2] == "1"
                else:
                    case_sensitive = tail == "1"
                pattern = parts[1]
            else:
                pattern = parts[1]
        exclude = bool(re.search(r"!+\{[^.]+\}", pattern))
        return FilterRule(field=f, pattern=pattern, exclude=exclude,
                          case_sensitive=case_sensitive, operator=operator)


@dataclass(frozen=True)
class SourceRef:
    """Referencia de un target a una fila de la cartera de fuentes.

    ``id`` es el id de la entrada en ``[source.*]``; ``name`` se copia para
    poder prefijar el display name ("Lista.Canal") sin leer la cartera.
    """

    id: str
    name: str
    # copia de la ubicación al guardar; evita releer la cartera en el pipeline
    location: str = ""


@dataclass(frozen=True)
class PipelineTarget:
    """Un objetivo de pipeline: fuente + filtros + EPG + servicio."""

    name: str
    source_index: int | str = 0
    rules: tuple[FilterRule, ...] = ()
    epg_source_index: int | None = None
    # canal (clave estable, p. ej. url) -> tvg-id del XMLTV (override manual)
    epg_overrides: dict[str, str] = field(default_factory=dict)
    port: int = 8080
    starting_number: int = 1
    enabled: bool = True
    # selección de la cartera global; vacío = flujo antiguo de fuente única
    source_refs: tuple[SourceRef, ...] = ()
    epg_refs: tuple[SourceRef, ...] = ()
    # canales desactivados manualmente (clave estable, p. ej. url); el
    # pipeline los excluye de la lista servida
    disabled_channels: tuple[str, ...] = ()
    # prioridad de calidad (token -> peso, 0 = nunca). None = de fábrica: se
    # sirve una sola variante de cada canal (ver list_manager/quality.py)
    quality_priority: dict[str, int] | None = None


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
    # lista de la cartera que lo aportó; alimenta el campo de filtro 'source'.
    # No se antepone al nombre de salida: el reproductor muestra solo el canal.
    source_name: str = ""
