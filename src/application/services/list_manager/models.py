"""Modelos de dominio del gestor de listas.

Qt-free, sin I/O. Los patrones de filtro persisten en sintaxis compatible con
xTeVe (ver odd/tasks/list-manager.md, sección "Filtros").
"""

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from src.application.services.list_manager.quality import base_name

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
        """Formato plano de la regla: ``field:pattern:case[:op]``.

        Es lo que viaja en la API y lo que se guarda en ``daemon.toml``.

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
    ``priority`` viaja igual: el pipeline ordena las guías por ese peso para
    decidir cuál manda cuando dos anuncian el mismo canal.
    """

    id: str
    name: str
    # copia de la ubicación al guardar; evita releer la cartera en el pipeline
    location: str = ""
    priority: int = 0


#: Prefijo de la clave de ficha cuando el canal no tiene código de guía.
URL_KEY_PREFIX = "url-"
#: Prefijo de la clave de ficha derivada del nombre interno.
NAME_KEY_PREFIX = "name-"


def url_key(url: str) -> str:
    """Identidad de un canal sin código de guía: hash de su url.

    Es la última tabla de salvamento: el proveedor rota hosts y repite el
    mismo canal con varias urls, pero mientras se sirva la misma, la ficha
    sigue enganchada.
    """
    digest = hashlib.sha1((url or "").strip().encode("utf-8", "replace"))
    return f"{URL_KEY_PREFIX}{digest.hexdigest()[:12]}"


def channel_keys(canonical: str | None, url: str = "",
                 name: str = "") -> tuple[str, ...]:
    """Claves con las que buscar la ficha de un canal, de más a menos fiable.

    El código de la guía es lo estable; la url cambia (el mismo canal llega
    con varios hosts) y el nombre es el último recurso.
    """
    claves: list[str] = []
    if canonical:
        claves.append(canonical)
    if url:
        claves.append(url_key(url))
    interno = base_name(name) if name else ""
    if interno:
        claves.append(f"{NAME_KEY_PREFIX}{interno}")
    return tuple(claves)


@dataclass(frozen=True)
class ChannelProfile:
    """Ficha de un canal: lo que el usuario ha fijado a mano.

    Se guarda bajo su identidad (código de la guía o, si no lo hay, hash de la
    url). ``alias`` es el nombre interno con el que se vio, que sirve para
    reengancharla cuando cambia la url.

    Un campo vacío (o ``None``) significa "lo que diga el automático": el
    fichero guarda solo lo que el usuario ha tocado, no una copia del catálogo
    que envejecería sola.
    """

    alias: str = ""
    #: Número forzado; vacío = el dial de la guía, o la cola si no hay.
    dial: int | None = None
    #: Nombre que verá el reproductor (solo cosmético: el dedupe usa el interno).
    display: str = ""
    #: Fichero de ``assets/logos`` (el demonio lo sirve en ``/logos/``).
    logo: str = ""


def merge_profiles(fichas: list[ChannelProfile]) -> ChannelProfile:
    """Une las fichas de un canal: el primer valor no vacío gana.

    Un canal puede tener ficha por código de guía y otra por url (la que dejó
    un mapeo manual anterior). Cada campo se queda con el primero que lo trae,
    y ``enabled`` es falso si cualquiera lo desactiva.
    """
    if not fichas:
        return ChannelProfile()
    salida = fichas[0]
    for ficha in fichas[1:]:
        salida = ChannelProfile(
            alias=salida.alias or ficha.alias,
            dial=salida.dial if salida.dial is not None else ficha.dial,
            display=salida.display or ficha.display,
            logo=salida.logo or ficha.logo,
        )
    return salida


@dataclass(frozen=True)
class PipelineTarget:
    """Un perfil: fuente + filtros + EPG + servicio.

    En la interfaz y en la documentación se llama **Perfil**; ``target`` se
    mantiene como nombre interno y en la API (``/targets/{id}``) para no romper
    clientes ni ficheros de configuración.
    """

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
    # fichas por identidad de canal (código de guía o hash de url): dial
    # forzado, nombre mostrado y logo. El EPG asignado y el apagado a mano
    # siguen en ``epg_overrides``/``disabled_channels``: son por url y ya
    # funcionan; migrarlos a fichas es otro cambio, no una capa nueva.
    channels: dict[str, ChannelProfile] = field(default_factory=dict)
    #: Servir con guía de relleno (Dummy 1h) los canales que se quedan sin EPG.
    serve_without_epg: bool = False
    #: Guía de salida sin descripciones ni categorías (65 % menos de fichero).
    light_epg: bool = True
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
    #: Nombre con el que viene en la lista, si ``name`` es el mostrado. El
    #: dedupe y los pesos de calidad usan este, no el cosmético.
    internal_name: str | None = None
    logo_url: str | None = None
    #: Fichero de ``assets/logos`` elegido a mano; el demonio lo sirve desde
    #: ``/logos/`` sin descargarlo. Tiene prioridad sobre ``logo_url``.
    logo_file: str | None = None
    tvg_id: str | None = None
    tvg_name: str | None = None
    chno: int | None = None
    # tvg-id asignado tras el matching (None si el canal queda sin EPG)
    matched_epg_id: str | None = None
    # identidad del canal: el id de la guía que publica dial y logo, aunque
    # los programas vengan de otra. Agrupa variantes y pone número; no es el
    # ``tvg-id`` de salida (ese es ``matched_epg_id``)
    canonical_id: str | None = None
    # lista de la cartera que lo aportó; alimenta el campo de filtro 'source'.
    # No se antepone al nombre de salida: el reproductor muestra solo el canal.
    source_name: str = ""
