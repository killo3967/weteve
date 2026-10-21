"""Guía de relleno (Dummy 1h) para canales sin EPG real.

Un canal sin guía aparece en el reproductor como "sin información" y, según
cuál sea, puede ni siquiera listar el canal. El dummy le da una parrilla
sintética de bloques de una hora con el nombre del canal como título, para
que el canal sea visible y navegable aunque la guía no exista.

El id se deriva de la url del canal y es **estable entre regeneraciones**: el
demonio reconstruye el ``epg.xml`` en cada recarga y el reproductor cachea la
guía, así que un id aleatorio rompería el emparejamiento en cada ciclo. El
hash va truncado y prefijado (``weteve-dummy-``) para que el prefijo por sí
solo identifique la guía sintética sin consultar un catálogo.

Cada canal recibe su propio id —no uno compartido— porque el XMLTV exige un
``<channel>`` por id: si dos canales dummy compartieran id compartirían también
título, y el reproductor mostraría el mismo nombre en ambos.
"""

import hashlib

from src.domain.entities.epg import Program

DUMMY_LABEL = "Dummy 1h"
# Fila en blanco del desplegable: 'sin asignación'. Se define como constante
# para que el widget y quien resuelva valores hablen del mismo centinela.
BLANK_LABEL = ""
DUMMY_ID_PREFIX = "weteve-dummy-"
DUMMY_HOURS = 168  # 7 días hacia delante
DUMMY_BLOCK_MINUTES = 60


def is_dummy_label(value: str) -> bool:
    """True si el texto de un desplegable es la opción de relleno.

    Compara normalizado (sin casing ni espacios) porque el combo es editable:
    el usuario puede escribir ``dummy 1h`` y debe colar.
    """
    return (value or "").strip().lower() == DUMMY_LABEL.lower()


def is_dummy_id(channel_id: str | None) -> bool:
    """True si un channel-id es un id de relleno generado por nosotros."""
    return bool(channel_id and channel_id.startswith(DUMMY_ID_PREFIX))


def dummy_id_for(identity: str) -> str:
    """Id de relleno estable para un canal, derivado de su identidad.

    La identidad es el código de la guía o, si no lo hay, la clave de la ficha
    (hash de la url / del nombre). Derivarlo de la url era frágil: el mismo
    canal llega con varias urls y la deduplicación puede cambiar de variante,
    así que el id cambiaba y el reproductor lo veía como un canal nuevo.
    """
    digest = hashlib.sha1((identity or "").strip().encode("utf-8", "replace"))
    return f"{DUMMY_ID_PREFIX}{digest.hexdigest()[:12]}"


def make_dummy_id(url: str, name: str) -> str:
    """Id de relleno a partir de una url (o del nombre si no hay url).

    Se mantiene para los mapeos ya guardados y para quien no conozca la
    identidad del canal; el camino nuevo es ``dummy_id_for``.
    """
    key = (url or "").strip() or (name or "").strip()
    return dummy_id_for(key)


def build_dummy_channel(name: str, channel_id: str, now=None) -> list[Program]:
    """Parrilla sintética de ``DUMMY_HOURS`` bloques de una hora.

    Ancla en la hora en punto *en curso* (``now`` truncada a la hora), no en la
    siguiente: si el primer bloque empezase más tarde que ``now``, el
    reproductor no tendría franja que mostrar durante el resto de la hora
    actual, que es justamente cuando alguien consulta la guía.

    ``now`` es inyectable para que los tests sean deterministas.
    """
    from datetime import datetime, timedelta

    if now is None:
        # zona local: los bloques se anclan al reloj del equipo que sirve,
        # que es el que ve el reproductor.
        now = datetime.now().astimezone()

    start = now.replace(minute=0, second=0, microsecond=0)
    step = timedelta(minutes=DUMMY_BLOCK_MINUTES)
    title = name or DUMMY_LABEL
    programs: list[Program] = []
    for i in range(DUMMY_HOURS):
        block_start = start + i * step
        programs.append(
            Program(
                title=title,
                start_time=block_start,
                end_time=block_start + step,
                channel_id=channel_id,
                description=None,
                category=None,
            )
        )
    return programs
