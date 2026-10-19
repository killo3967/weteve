"""Política de refresco de guías: filtrar no descarga; refrescar es explícito.

Antes, cualquier recarga (reglas, mapeos, desactivados) pasaba por
``register_merged`` y podía acabar descargando el XMLTV entero si la caché
había caducado. Ahora la guía se reutiliza y solo se vuelve a leer cuando lo
pide el planificador o el botón «Actualizar guías».
"""

from datetime import datetime

from src.application.services.list_manager.models import (
    FilterRule,
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel
from src.domain.entities.epg import EPGData, Program


class _M3URepo:
    def __init__(self, channels): self._channels = channels

    def get_channels(self, source): return list(self._channels)


class _EpgRepo:
    """Cuenta cuántas veces se lee la guía y si fue con ``fresh``."""

    def __init__(self):
        self.llamadas: list[tuple[str, bool]] = []

    def load_epg(self, source, fresh=False):
        self.llamadas.append((source, fresh))
        return EPGData(
            programs=[Program("T", datetime(2026, 1, 1, 10),
                              datetime(2026, 1, 1, 11), "es.la1")],
            channel_names={"es.la1": "La 1"})


def _pipeline():
    epg = _EpgRepo()
    p = ListManagerPipeline(
        m3u_repository=_M3URepo([
            Channel("La 1", "http://x/la1", "General", tvg_id="es.la1")]),
        epg_repository=epg)
    return p, epg


def _target(**kw) -> PipelineTarget:
    base = dict(name="t", source_index="0",
                rules=(FilterRule.parse("group-title:General:0:and"),),
                source_refs=(SourceRef("m", "lista", "C:/l.m3u"),),
                epg_refs=(SourceRef("g", "guia", "https://x/guia.xml"),))
    base.update(kw)
    return PipelineTarget(**base)


class TestRegistroDeGuias:
    def test_filtrar_no_vuelve_a_leer_la_guia(self):
        p, epg = _pipeline()
        p.register_merged("0", _target().source_refs, _target().epg_refs)
        assert len(epg.llamadas) == 1
        # diez recargas de reglas: la guía se lee una sola vez
        for _ in range(10):
            p.register_merged("0", _target().source_refs, _target().epg_refs)
            p.run(_target())
        assert len(epg.llamadas) == 1

    def test_refresh_epg_la_vuelve_a_leer(self):
        p, epg = _pipeline()
        t = _target()
        p.register_merged("0", t.source_refs, t.epg_refs)
        p.register_merged("0", t.source_refs, t.epg_refs, refresh_epg=True)
        assert [fresh for _src, fresh in epg.llamadas] == [False, True]

    def test_cambiar_de_guia_la_vuelve_a_leer(self):
        p, epg = _pipeline()
        p.register_merged("0", _target().source_refs, _target().epg_refs)
        otras = (SourceRef("g2", "otra", "https://x/otra.xml"),)
        p.register_merged("0", _target().source_refs, otras)
        assert len(epg.llamadas) == 2

    def test_fuente_unica_tambien_reutiliza(self):
        p, epg = _pipeline()
        p.register_source("0", "C:/l.m3u", "https://x/guia.xml")
        p.register_source("0", "C:/l.m3u", "https://x/guia.xml")
        assert len(epg.llamadas) == 1
        p.register_source("0", "C:/l.m3u", "https://x/guia.xml",
                          refresh_epg=True)
        assert len(epg.llamadas) == 2

    def test_cambiar_la_ruta_de_guia_recarga(self):
        p, epg = _pipeline()
        p.register_source("0", "C:/l.m3u", "https://x/una.xml")
        p.register_source("0", "C:/l.m3u", "https://x/otra.xml")
        assert len(epg.llamadas) == 2
