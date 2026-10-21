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


#: Raíz mínima que se exige delante de una etiqueta pegada (``LaLigaTVHD``).
#: Con menos no se distingue de una palabra que acaba igual (``CLASSD``).
_RAIZ_MINIMA = 5

_PATRONES: dict[str, re.Pattern[str]] = {}


def _patron(token: str) -> re.Pattern[str]:
    """Regex de una etiqueta de calidad, suelta o pegada al final de palabra.

    El proveedor escribe la calidad de las dos formas: ``LA 1 HD`` pero
    también ``M+ LaLigaTVHD``, ``M+DEPORTESHD`` o ``M+ #VAMOSHD``. Con la
    forma pegada, el mismo canal contaba como dos: se servían las cuatro
    variantes de ``M+ LaLigaTV`` y el dial no se reconocía.

    Solo cuenta el final de palabra: una etiqueta en medio del nombre no es
    una etiqueta de calidad (``HDO Sport`` no es un canal en HD).
    """
    patron = _PATRONES.get(token)
    if patron is None:
        patron = re.compile(
            rf"(?:(?<![a-z0-9])|(?<=[a-z0-9]{{{_RAIZ_MINIMA}}}))"
            rf"{re.escape(token)}(?![a-z0-9])")
        _PATRONES[token] = patron
    return patron


def quality_token(name: str) -> str:
    """Etiqueta de calidad del nombre ('' si no lleva ninguna).

    Gana la más específica: ``LA 1 FULL HD`` es ``full hd``, no ``hd``.
    """
    norm = normalize(name)
    for token in sorted((t for t in DEFAULT_PRIORITY if t), key=len,
                        reverse=True):
        if _patron(token).search(norm):
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
    # el ``+`` suelto es la coletilla de ``HD+``; un ``+`` pegado forma parte
    # de la marca (``M+``) y no se toca
    return " ".join(part for part in _patron(token).sub(" ", norm).split()
                    if part != "+")


def dedupe_variants(channels, priority: dict[str, int] | None = None
                    ) -> tuple[list, list]:
    """Una variante por (id EPG, nombre base); devuelve (servidos, duplicados).

    - Gana la de mayor prioridad; con empate, la primera del fichero (orden
      estable y predecible).
    - Prioridad ``0``: esa variante no se sirve nunca, pero tampoco «gana»: si
      todas las variantes de un canal valen 0, el canal desaparece.
    - Canales sin id EPG no se agrupan: sin guía no hay nada que duplicar (los
      filtra la regla de servidor).
    - Con ``canonical_id`` la clave es solo la identidad: el nombre ya no
      distingue variantes (``BeMad tv HD`` y ``BE MAD HD`` son la misma). Sin
      identidad se mantiene la clave antigua, id de guía + nombre base, porque
      dos canales distintos pueden compartir guía (``La 1`` y
      ``La 1 Canarias``).
    """
    tabla = priority or DEFAULT_PRIORITY
    mejores: dict[tuple[str, str], tuple[int, int]] = {}
    servir: set[int] = set()
    for index, ch in enumerate(channels):
        # Con identidad canónica el nombre sobra: ``BeMad tv HD`` y
        # ``BE MAD HD`` son el mismo canal aunque se escriban distinto, y
        # dejar las dos copias servidas rompía la numeración (una se quedaba
        # con el dial y la otra caía al contador).
        canonico = getattr(ch, "canonical_id", None)
        if canonico:
            clave = (canonico, "")
        else:
            epg_id = getattr(ch, "matched_epg_id", None)
            if not epg_id:
                servir.add(index)      # sin guía: lo decide la regla de servidor
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
