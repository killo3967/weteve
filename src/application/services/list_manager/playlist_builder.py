"""Construcción de la M3U y del XMLTV de salida del gestor de listas."""

from datetime import datetime

from src.application.services.list_manager.models import MappedChannel


def _fmt_xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0200")


def build_m3u(channels: list[MappedChannel]) -> str:
    """Genera el contenido M3U de salida a partir de los canales mapeados."""
    lines = ["#EXTM3U"]
    for i, ch in enumerate(channels, start=1):
        attrs = [f'tvg-id="{ch.matched_epg_id or ch.tvg_id or ""}"']
        if ch.tvg_name:
            attrs.append(f'tvg-name="{ch.tvg_name}"')
        if ch.logo_url:
            attrs.append(f'tvg-logo="{ch.logo_url}"')
        group = ch.group or "Otros"
        attrs.append(f'group-title="{group}"')
        if ch.chno is not None:
            attrs.append(f'tvg-chno="{ch.chno}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{ch.name}')
        lines.append(ch.url)
    return "\n".join(lines) + "\n"


def build_xmltv(channels: list[MappedChannel], epg) -> str:
    """Genera el XMLTV de salida solo con los canales mapeados.

    Args:
        channels: canales resultantes del pipeline.
        epg: ``EPGData`` con la guía original.

    Returns:
        Contenido XML del EPG regenerado. Los canales sin match no aparecen.
    """
    import xml.etree.ElementTree as ET

    tv = ET.Element("tv", {"generator-info-name": "IPTVViewer list-manager"})
    for ch in channels:
        epg_id = ch.matched_epg_id or ch.tvg_id
        if not epg_id:
            continue
        channel_el = ET.SubElement(tv, "channel", {"id": epg_id})
        display = ET.SubElement(channel_el, "display-name")
        display.text = ch.name

    for ch in channels:
        epg_id = ch.matched_epg_id or ch.tvg_id
        if not epg_id:
            continue
        for prog in epg.get_programs_for_channel(epg_id):
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
