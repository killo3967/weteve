"""Asociación M3U <-> EPG (XMLTV) del gestor de listas.

Cuatro fases (especificación: odd/tasks/list-manager.md):
  1. ``tvg-id`` exacto del canal contra los channel-ids del XMLTV.
  2. Nombre normalizado exacto (sin acentos, lowercase, sin puntuación).
  3. Nombre base exacto, sin la etiqueta de calidad: el proveedor lista
     ``LA 1 HD``/``LA 1 FHD`` y la guía los anuncia como ``LA 1``.
  4. Parecido por palabras de marca, con dos reglas duras: el sufijo
     numérico tiene que coincidir (``M+ LaLiga 2`` no es ``M+ LaLiga``) y el
     ganador necesita margen sobre el segundo candidato.

Las cuatro fases existen porque cada guía bautiza al mismo canal a su manera:
la lista IPTV dice ``M+ LaLigaTVHD``, Koala ``M+ LALIGA TV HD``, Doble_M
``M+ LaLiga`` y la guía de Movistar+ ``M+ LALIGA`` (código ``MLIGA``, dial
54). Sin el parecido por marca, ese canal son cuatro canales distintos.

Sin match: el canal queda sin EPG y el editor manual puede asignarlo mediante
``epg_overrides`` (persistidos con prioridad sobre el automático).
"""

import difflib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from src.application.services.list_manager.quality import base_name
from src.domain.entities.epg import EPGData

#: Parecido mínimo para aceptar un emparejamiento que no es exacto.
UMBRAL_PARECIDO = 0.80
#: Diferencia mínima con el segundo candidato cuando hay que decidir el dial.
MARGEN_MINIMO = 0.05

#: Palabras que no distinguen un canal de otro.
_PARADAS = frozenset({"de", "del", "la", "el", "los", "las", "y", "e", "en",
                      "con", "the", "of"})

#: Abreviaturas del proveedor → palabras que usa la guía.
_ABREVIATURAS: dict[str, tuple[str, ...]] = {
    "lcampeon": ("liga", "campeones"),
    "lcampeones": ("liga", "campeones"),
    "doc": ("documentales",),
    "docs": ("documentales",),
    "dep": ("deportes",),
    "ch": ("channel",),
}

#: Anotaciones entre paréntesis del proveedor ("M+ DEPORTES3 (SOLO EVENTOS)").
_RE_ANOTACION = re.compile(r"[\(\[\{].*?[\)\]\}]")

#: La marca del proveedor, escrita de todas las formas posibles.
_MARCAS = (
    (re.compile(r"\bm\+"), " mplus "),                 # M+
    (re.compile(r"\bmovistar\s*\+"), " mplus "),       # Movistar+
    (re.compile(r"\bmovistar\s+plus"), " mplus "),     # Movistar Plus
)
_RE_DIGITO_LETRA = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_RE_PALABRA = re.compile(r"[a-z0-9]+")


def normalize_channel_name(name: str) -> str:
    """Normalización de nombres para matching (NFKD + sin diacríticos + lowercase).

    Más tolerante que ``normalize_name`` de ``epg.py``: conserva espacios
    colapsados para permitir comparaciones legibles y sugerencias.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = without_accents.lower()
    collapsed = " ".join(lowered.split())
    return collapsed


@dataclass(frozen=True)
class BrandKey:
    """Lo que identifica a un canal por su nombre, sin la guía de turno.

    ``palabras`` son las palabras significativas, ``numero`` el sufijo
    numérico (``""`` si no lo lleva) y ``compacto`` el nombre sin espacios,
    que es lo que iguala ``CAZAyPESCA`` con ``Caza y Pesca``.
    """

    palabras: frozenset[str]
    numero: str
    compacto: str
    trigramas: frozenset[str]


def brand_key(name: str) -> BrandKey:
    """Palabras de marca, sufijo numérico y forma compacta de un nombre.

    Unifica lo que cada guía escribe a su manera: la marca (``M+``,
    ``Movistar+``, ``Movistar Plus``), la etiqueta de calidad (también pegada:
    ``LaLigaTVHD``), el sufijo ``TV`` (``M+ LaLigaTV`` = ``M+ LaLiga``), las
    abreviaturas del proveedor (``M+LCAMPEON4``), los números pegados a la
    letra (``M+LCAMPEON4`` → ``4``) y las anotaciones entre paréntesis
    (``M+ DEPORTES3 (SOLO EVENTOS)``).
    """
    texto = base_name(_RE_ANOTACION.sub(" ", name))
    if not texto:
        return BrandKey(frozenset(), "", "", frozenset())
    for patron, reemplazo in _MARCAS:
        texto = patron.sub(reemplazo, texto)
    texto = _RE_DIGITO_LETRA.sub(" ", texto)
    palabras_crudas = _RE_PALABRA.findall(texto)
    numero = ""
    if (len(palabras_crudas) > 1 and palabras_crudas[-1].isdigit()
            and len(palabras_crudas[-1]) <= 3):
        numero = palabras_crudas[-1]
    palabras: list[str] = []
    for palabra in palabras_crudas:
        if palabra in _PARADAS:
            continue
        # "LaLigaTV" y "LaLiga" son el mismo canal; "TVG" o "TV3" no
        if len(palabra) > 5 and palabra.endswith("tv"):
            palabra = palabra[:-2]
        if palabra == "tv":
            continue
        palabras.extend(_ABREVIATURAS.get(palabra, (palabra,)))
    compacto = "".join(palabras)
    trigramas = frozenset(compacto[i:i + 3] for i in range(len(compacto) - 2))
    return BrandKey(frozenset(palabras), numero, compacto, trigramas)


def parecido(a: BrandKey, b: BrandKey) -> float:
    """Cuánto se parecen dos canales por su nombre (0.0 a 1.0).

    El sufijo numérico manda: sin él, ``M+ LaLigaTV`` casaría con
    ``M+ LALIGA 2`` y el dial saldría mal. La forma compacta cubre las
    palabras pegadas, que no comparten ni una palabra suelta.
    """
    if a.numero != b.numero:
        return 0.0
    if not a.palabras or not b.palabras:
        return 0.0
    jaccard = len(a.palabras & b.palabras) / len(a.palabras | b.palabras)
    if jaccard == 1.0:
        return 1.0
    if not (a.trigramas & b.trigramas):
        return jaccard
    return max(jaccard, difflib.SequenceMatcher(
        None, a.compacto, b.compacto).ratio())


class EpgMatcher:
    """Asocia canales M3U con channel-ids de una EPG XMLTV."""

    def __init__(self, epg: EPGData):
        self._epg = epg
        # channel_names/channel_info devuelven copias: se leen una sola vez
        self._nombres = epg.channel_names
        info = epg.channel_info
        self._diales = {cid: meta.dial for cid, meta in info.items()}
        # Ids de la guía que publica dial/logo (Movistar+): cuando dos guías
        # anuncian el mismo canal, gana la que aporta los metadatos, porque es
        # la única que puede dar número real y logo.
        self._con_metadatos = set(info)
        # Canónicos: solo los ids que publican DIAL. Las guías XMLTV traen
        # logo (channel_info) pero no número; tomarlas por canónicas partía la
        # identidad en dos: "CUATRO HD" (Koala) y "C4" (Movistar) son el mismo
        # canal y el número solo lo da la segunda.
        self._canonicos = {cid for cid, meta in info.items()
                           if meta.dial is not None}
        # Peso de la guía que anuncia cada id: es el desempate principal entre
        # candidatos de guías distintas (lo fija la cartera, no el canal).
        self._prioridades = epg.channel_priority
        # Normaliza los channel-ids del XMLTV para match por tvg-id
        # case-insensitive.
        self._ids_by_normalized_id: dict[str, str] = {}
        for channel_id in epg.programs_by_channel_ids():
            norm = channel_id.strip().lower()
            if norm and norm not in self._ids_by_normalized_id:
                self._ids_by_normalized_id[norm] = channel_id
        # nombre normalizado -> channel-id, y nombre sin la etiqueta de
        # calidad -> channel-id (el proveedor lista "LA 1 HD"/"LA 1 FHD" y la
        # guía los anuncia como "LA 1").
        self._id_by_normalized_name: dict[str, str] = {}
        self._id_by_base_name: dict[str, str] = {}
        # marca cacheada por id: el parecido se consulta canal a canal
        self._claves_marca: dict[str, BrandKey] = {}
        for channel_id, display_name in self._nombres.items():
            norm_name = normalize_channel_name(display_name)
            if norm_name:
                self._preferir(self._id_by_normalized_name, norm_name, channel_id)
            base = base_name(display_name)
            if base:
                self._preferir(self._id_by_base_name, base, channel_id)
            self._claves_marca[channel_id] = brand_key(display_name)

    def _preferir(self, mapa: dict[str, str], clave: str, channel_id: str) -> None:
        """Guarda el id para ``clave``; gana la guía de más prioridad.

        A igual prioridad gana la que publica dial y logo (es la única que
        puede dar número real); sin ella, el primero que llega, que es el
        orden de la cartera.
        """
        previo = mapa.get(clave)
        if previo is None:
            mapa[clave] = channel_id
            return
        nueva = self._prioridades.get(channel_id, 0)
        actual = self._prioridades.get(previo, 0)
        if nueva > actual:
            mapa[clave] = channel_id
        elif nueva == actual and (channel_id in self._con_metadatos
                                  and previo not in self._con_metadatos):
            mapa[clave] = channel_id

    def _mejor(self, clave: BrandKey, candidatos: Iterable[str],
               exigir_margen: bool = False) -> str | None:
        """Candidato más parecido; ``None`` si no llega al umbral.

        Con ``exigir_margen`` el empate técnico no se resuelve: se devuelve
        ``None``. Es lo que se usa para el dial, donde un número equivocado es
        peor que un número de menos.
        """
        puntuados: list[tuple[float, str]] = []
        for cid in candidatos:
            # un id con metadatos puede no tener nombre mostrado (guía rara):
            # sin nombre no hay parecido que calcular
            marca = self._claves_marca.get(cid)
            if marca is None:
                continue
            score = parecido(clave, marca)
            if score >= UMBRAL_PARECIDO:
                puntuados.append((score, cid))
        if not puntuados:
            return None
        puntuados.sort(key=lambda par: (
            -par[0],
            -self._prioridades.get(par[1], 0),
            par[1] not in self._con_metadatos,
            self._diales.get(par[1]) is None,
            par[1],
        ))
        if (exigir_margen and len(puntuados) > 1
                and puntuados[0][0] - puntuados[1][0] < MARGEN_MINIMO):
            return None
        return puntuados[0][1]

    def match(self, tvg_id: str | None, name: str) -> str | None:
        """Devuelve el channel-id del XMLTV para un canal, o None."""
        if tvg_id:
            norm_id = tvg_id.strip().lower()
            if norm_id in self._ids_by_normalized_id:
                return self._ids_by_normalized_id[norm_id]
        norm_name = normalize_channel_name(name)
        base = base_name(name)
        candidatos: list[tuple[str, bool]] = []
        exacto = self._id_by_normalized_name.get(norm_name)
        if exacto:
            candidatos.append((exacto, True))
        por_base = self._id_by_base_name.get(base) if base else None
        if por_base and por_base != exacto:
            candidatos.append((por_base, False))
        if not candidatos:
            # Cuarta fase: parecido por marca. Aquí no se exige margen porque
            # solo decide de qué guía salen los programas; el dial lo decide
            # ``canonical_for``, que sí lo exige.
            return self._mejor(brand_key(name), self._claves_marca)

        def peso(candidato: tuple[str, bool]) -> tuple[int, bool, bool]:
            cid, es_exacto = candidato
            # prioridad de la guía > guía que publica dial/logo > nombre exacto
            return (self._prioridades.get(cid, 0), cid in self._con_metadatos,
                    es_exacto)

        return max(candidatos, key=peso)[0]

    def canonical_for(self, name: str, epg_id: str | None = None) -> str | None:
        """Id del canal en la guía que publica sus metadatos (dial y logo).

        Es la identidad del canal, no su parrilla: agrupa variantes y pone
        número aunque los programas vengan de otra guía.

        ``epg_id`` es el id que resolvió el emparejamiento y sirve de atajo,
        pero solo si el nombre lo confirma: un mapeo manual puede apuntar dos
        canales distintos al mismo id de guía, y eso no basta para decir que
        son el mismo canal. Sin atajo se busca por marca, con margen sobre el
        segundo candidato: con dos canales igual de parecidos no se decide el
        dial. Los candidatos son solo los ids que publican dial.
        """
        clave = brand_key(name)
        if epg_id and epg_id in self._canonicos:
            if parecido(clave, self._claves_marca[epg_id]) >= UMBRAL_PARECIDO:
                return epg_id
        if not self._canonicos:
            return None
        return self._mejor(clave, self._canonicos, exigir_margen=True)

    def suggestions(self, name: str, limit: int = 5) -> list[tuple[str, str]]:
        """Hasta ``limit`` sugerencias (channel-id, nombre) por similitud."""
        norm_name = normalize_channel_name(name)
        if not norm_name:
            return []
        scored: list[tuple[float, str, str]] = []
        for channel_id, display_name in self._nombres.items():
            candidate = normalize_channel_name(display_name)
            ratio = difflib.SequenceMatcher(None, norm_name, candidate).ratio()
            scored.append((ratio, channel_id, display_name))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [(cid, display) for ratio, cid, display in scored[:limit] if ratio > 0.3]
