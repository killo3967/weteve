"""Construcción de la M3U y del XMLTV de salida del gestor de listas."""

from datetime import datetime

from src.application.services.list_manager.dummy_epg import (
    build_dummy_channel,
    is_dummy_id,
    make_dummy_id,
)
from src.application.services.list_manager.models import MappedChannel


def _fmt_xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0200")


def build_dummy_epg_id(url: str, name: str) -> str:
    """Id de la guía de relleno (Dummy 1h) para un canal.

    Se expone aquí porque quien lo necesita es la UI al resolver el
    desplegable: 'Dummy 1h' no es un canal XMLTV, así que hay que traducirla al
    id sintético con el que ``build_xmltv`` reconoce el canal. Derivado de la
    url, es estable entre regeneraciones del EPG.
    """
    return make_dummy_id(url, name)


def epg_id_of(ch: MappedChannel) -> str | None:
    """Id EPG del canal: el asignado a mano o el tvg-id propio, si lo hay."""
    return ch.matched_epg_id or ch.tvg_id or None


def build_m3u(channels: list[MappedChannel]) -> str:
    """Genera el contenido M3U de salida a partir de los canales mapeados.

    El ``tvg-id`` sale siempre vacío, como en las listas de proveedor: el
    emparejamiento con la guía lo hace el reproductor o el agregador, no este
    fichero. El nombre es el del canal, sin el prefijo de la lista de origen.

    Consecuencia para el Dummy 1h: el enlace canal↔guía va por **nombre**
    (tvg-id vacío), no por id, así que el ``<display-name>`` del dummy tiene
    que ser el nombre del canal M3U. Si fuese la etiqueta 'Dummy 1h', ningún
    reproductor encontraría ahí el canal.
    """
    lines = ["#EXTM3U"]
    for ch in channels:
        attrs = ['tvg-id=""']
        if ch.tvg_name:
            attrs.append(f'tvg-name="{ch.tvg_name}"')
        if ch.logo_url:
            attrs.append(f'tvg-logo="{ch.logo_url}"')
        attrs.append(f'group-title="{ch.group or "Otros"}"')
        if ch.chno is not None:
            attrs.append(f'tvg-chno="{ch.chno}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{ch.name}')
        lines.append(ch.url)
    return "\n".join(lines) + "\n"


def build_xmltv(channels: list[MappedChannel], epg) -> str:
    """Genera el XMLTV de salida con la guía real de los canales mapeados.

    Args:
        channels: canales resultantes del pipeline.
        epg: ``EPGData`` con la guía original, o ``None`` si no hay fuente.

    Returns:
        Contenido XML del EPG. Solo aparecen los canales con id EPG (asignado
        a mano o propio). Un id de relleno (Dummy 1h) no está en la guía
        original: se le genera la suya sintética.
    """
    import xml.etree.ElementTree as ET

    tv = ET.Element("tv", {"generator-info-name": "IPTVViewer list-manager"})
    # Dos canales pueden compartir id EPG (asignados al mismo canal de guía, o
    # nombres duplicados): el XMLTV exige un <channel> por id y una sola
    # parrilla por franja.
    declared: set[str] = set()
    for ch in channels:
        epg_id = epg_id_of(ch)
        if epg_id is None or epg_id in declared:
            continue
        declared.add(epg_id)
        channel_el = ET.SubElement(tv, "channel", {"id": epg_id})
        display = ET.SubElement(channel_el, "display-name")
        display.text = ch.name

    seen_slots: set[tuple[str, str]] = set()
    for ch in channels:
        epg_id = epg_id_of(ch)
        if epg_id is None:
            continue
        if is_dummy_id(epg_id):
            # Guía de relleno: no se busca en la EPG original (un id sintético
            # nunca está ahí) y se emite aunque ``epg`` sea None.
            programs = build_dummy_channel(ch.name, epg_id)
        elif epg is None:
            continue
        else:
            programs = epg.get_programs_for_channel(epg_id)
        for prog in programs:
            slot = (epg_id, _fmt_xmltv_time(prog.start_time))
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "start": _fmt_xmltv_time(prog.start_time),
                    "stop": _fmt_xmltv_time(prog.end_time),
                    "channel": epg_id,
                },
            )
            title = ET.SubElement(programme, "title")
            title.text = prog.title
            if prog.description:
                desc = ET.SubElement(programme, "desc")
                desc.text = prog.description
            if prog.category:
                cat = ET.SubElement(programme, "category")
                cat.text = prog.category

    ET.indent(tv, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        tv, encoding="unicode"
    ) + "\n"
