"""Prioridad de calidad entre variantes del mismo canal.

El proveedor mete el mismo canal varias veces con distinta etiqueta
(``LA 1 HD``, ``LA 1 FULL HD``, ``LA 1 SD``). Se sirve **una** variante: la de
mayor prioridad. Las prioridades son una preferencia del target (definición
4K en el salón, SD en el móvil para gastar menos datos); aquí viven las de
fábrica, y el pipeline las consulta siempre por parámetro para que un target
pueda tener otras.

Dos matices que no son obvios:

- **``0`` no es lo mismo que ``1``.** ``0`` = no servir nunca esa calidad (si
  un canal solo existe así, desaparece del target); ``1`` = último recurso
  (solo entra si no hay nada mejor).
- La agrupación es por **id EPG + nombre base**, no solo por id: dos canales
  distintos pueden compartir guía (``La 1`` y ``La 1 Canarias`` pueden apuntar
  al mismo ``<channel>``) y no son variantes del mismo canal.
"""

import re
import unicodedata

#: Calidad sin etiqueta: ni buena ni mala, va entre HD y SD.
SIN_ETIQUETA = ""

#: Prioridad de fábrica: token normalizado -> peso (0 = nunca, 1 = último
#: recurso). Varios tokens pueden compartir peso (``4k`` y ``uhd``).
DEFAULT_PRIORITY: dict[str, int] = {
    "4k": 5,
    "uhd": 5,
    "2160": 5,
    "full hd": 4,
    "fullhd": 4,
    "fhd": 4,
    "1080": 4,
    "hd": 3,
    "720": 3,
    SIN_ETIQUETA: 2,
    "sd": 1,
    "576": 1,
    "480": 1,
}


def normalize(name: str) -> str:
    """Minúsculas, sin acentos y solo palabras separadas por un espacio."""
    sin_acentos = unicodedata.normalize("NFKD", name or "").encode(
        "ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9+]+", " ", sin_acentos.lower()).split())


def quality_token(name: str) -> str:
    """Etiqueta de calidad del nombre ('' si no lleva ninguna).

    Gana la más específica: ``LA 1 FULL HD`` es ``full hd``, no ``hd``.
    """
    norm = normalize(name)
    for token in sorted((t for t in DEFAULT_PRIORITY if t), key=len,
                        reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", norm):
            return token
    return SIN_ETIQUETA


def weight(name: str, priority: dict[str, int] | None = None) -> int:
    """Prioridad de un canal por su nombre.

    Tabla vacía = tabla de fábrica: un target sin prioridad configurada no
    puede quedarse con todo a cero (que sería «no servir ninguna calidad»).
    """
    tabla = priority or DEFAULT_PRIORITY
    return int(tabla.get(quality_token(name), tabla.get(SIN_ETIQUETA, 0)))


def base_name(name: str) -> str:
    """Nombre sin la etiqueta de calidad: la clave para agrupar variantes."""
    norm = normalize(name)
    token = quality_token(name)
    if not token:
        return norm
    return " ".join(norm.replace(token, " ").split())


def dedupe_variants(channels, priority: dict[str, int] | None = None
                    ) -> tuple[list, list]:
    """Una variante por (id EPG, nombre base); devuelve (servidos, duplicados).

    - Gana la de mayor prioridad; con empate, la primera del fichero (orden
      estable y predecible).
    - Prioridad ``0``: esa variante no se sirve nunca, pero tampoco «gana»: si
      todas las variantes de un canal valen 0, el canal desaparece.
    - Canales sin id EPG no se agrupan: sin guía no hay nada que duplicar (los
      filtra la regla de servidor).
    """
    tabla = priority or DEFAULT_PRIORITY
    mejores: dict[tuple[str, str], tuple[int, int]] = {}
    servir: set[int] = set()
    for index, ch in enumerate(channels):
        epg_id = getattr(ch, "matched_epg_id", None)
        if not epg_id:
            servir.add(index)          # sin guía: lo decide la regla de servidor
            continue
        clave = (epg_id, base_name(ch.name))
        peso = weight(ch.name, tabla)
        actual = mejores.get(clave)
        if actual is None or peso > actual[0]:
            mejores[clave] = (peso, index)
    for peso, index in mejores.values():
        if peso > 0:
            servir.add(index)
    servidos = [ch for i, ch in enumerate(channels) if i in servir]
    duplicados = [ch for i, ch in enumerate(channels) if i not in servir]
    return servidos, duplicados
