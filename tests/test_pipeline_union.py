"""Unión de varias listas en el pipeline (cartera global de fuentes).

Un target con source_refs mezcla las filas IPTV activas en orden de tabla;
cada canal sale etiquetado con su lista (campo de agrupación "source") para
el filtro 'Lista' y el prefijo de render. La EPG se fusiona de las epg_refs.
"""

from datetime import datetime, timedelta

from src.application.services.list_manager.models import (
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.domain.entities.channel import Channel
from src.domain.entities.epg import EPGData, Program


def _epg(canal: str, cid: str) -> EPGData:
    now = datetime(2026, 10, 8, 12)
    return EPGData(
        programs=[Program(f"Prog {cid}", now, now + timedelta(hours=1), cid)],
        channel_names={cid: canal},
    )


class _MultiM3URepo:
    """Devuelve canales distintos según la ubicación pedida."""

    def __init__(self, by_location: dict[str, list[Channel]]):
        self._by = by_location

    def get_channels(self, source: str) -> list[Channel]:
        return list(self._by.get(source, []))


class _MultiEpgRepo:
    """Devuelve una guía distinta según la ubicación pedida."""

    def __init__(self, by_location: dict[str, EPGData]):
        self._by = by_location

    def load_epg(self, source: str, fresh: bool = False) -> EPGData:
        return self._by.get(source, EPGData())


def _pipeline(m3us: dict[str, list[Channel]], epgs: dict[str, EPGData] | None = None):
    return ListManagerPipeline(
        m3u_repository=_MultiM3URepo(m3us),
        epg_repository=_MultiEpgRepo(epgs or {}),
    )


def _target(refs: tuple[SourceRef, ...], epg_refs: tuple[SourceRef, ...] = ()) -> PipelineTarget:
    return PipelineTarget(
        name="t", source_index="merged",
        source_refs=refs, epg_refs=epg_refs,
    )


class TestUnionDeFuentes:
    def test_dos_listas_se_unen_en_orden_de_tabla(self):
        p = _pipeline({
            "C:/a.m3u": [Channel("La 1", "http://a/1", "General")],
            "C:/b.m3u": [Channel("La 2", "http://b/2", "General"),
                         Channel("Antena", "http://b/3", "General")],
        })
        p.register_merged("merged",
                          (SourceRef("s1", "principal", "C:/a.m3u"),
                           SourceRef("s2", "secunda", "C:/b.m3u")), ())
        res = p.run(_target((SourceRef("s1", "principal", "C:/a.m3u"),
                             SourceRef("s2", "secunda", "C:/b.m3u"))))
        assert res.total_input == 3
        assert [c.name for c in res.channels] == ["La 1", "La 2", "Antena"]
        # numeración global y continua
        assert [c.chno for c in res.channels] == [1, 2, 3]

    def test_cada_canal_lleva_su_lista_para_el_prefijo(self):
        p = _pipeline({
            "C:/a.m3u": [Channel("La 1", "http://a/1", "General")],
            "C:/b.m3u": [Channel("La 1", "http://b/1", "General")],
        })
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),
                SourceRef("s2", "secunda", "C:/b.m3u"))
        p.register_merged("merged", refs, ())
        res = p.run(_target(refs))
        assert [c.source_name for c in res.channels] == ["principal", "secunda"]

    def test_campo_de_agrupacion_source_expone_la_lista(self):
        """El filtro 'Lista' lee groups['source'], igual que tvg-group."""
        p = _pipeline({"C:/a.m3u": [Channel("La 1", "http://a/1", "General")]})
        p.register_merged("merged", (SourceRef("s1", "principal", "C:/a.m3u"),), ())
        ch = p._channels_of("merged")[0]
        assert ch.groups.get("source") == ["principal"]

    def test_sin_refs_se_mantiene_el_flujo_antiguo(self):
        """Regresión: register_source(index, path, epg) intacto."""
        p = _pipeline({"C:/solo.m3u": [Channel("A", "http://s/1", "General")]})
        p.register_source(0, "C:/solo.m3u", None)
        res = p.run(PipelineTarget(name="t", source_index=0))
        assert [c.name for c in res.channels] == ["A"]
        assert res.channels[0].source_name == ""

    def test_ref_huerfano_al_registrar_no_rompe(self):
        p = _pipeline({"C:/a.m3u": [Channel("A", "http://s/1", "General")]})
        # s2 no existe en el repo: su lista queda vacía pero no explota
        p.register_merged("merged",
                          (SourceRef("s1", "principal", "C:/a.m3u"),
                           SourceRef("s2", "fantasma", "C:/x.m3u")), ())
        res = p.run(_target((SourceRef("s1", "principal", "C:/a.m3u"),)))
        assert res.total_input == 1

    def test_filtro_excluye_por_lista(self):
        """Regla 'Lista' funcional: custom-filter:-{principal} deja fuera su lista."""
        from src.application.services.list_manager.models import FilterRule
        p = _pipeline({
            "C:/a.m3u": [Channel("La 1", "http://a/1", "General")],
            "C:/b.m3u": [Channel("Antena", "http://b/1", "General")],
        })
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),
                SourceRef("s2", "secunda", "C:/b.m3u"))
        p.register_merged("merged", refs, ())
        t = PipelineTarget(
            name="t", source_index="merged", source_refs=refs,
            rules=(FilterRule.parse("source:!{principal}:0"),),
        )
        res = p.run(t)
        assert [c.name for c in res.channels] == ["Antena"]


class TestEpgFusionada:
    def test_epg_de_varias_guias_se_fusiona(self):
        p = _pipeline(
            {"C:/a.m3u": [Channel("La 1", "http://a/1", "General", tvg_id="es.la1"),
                          Channel("A3", "http://a/2", "General", tvg_id="es.a3")]},
            {"C:/g1.xml": _epg("La 1", "es.la1"),
             "C:/g2.xml": _epg("Antena 3", "es.a3")},
        )
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),)
        epg_refs = (SourceRef("e1", "guia1", "C:/g1.xml"),
                    SourceRef("e2", "guia2", "C:/g2.xml"))
        p.register_merged("merged", refs, epg_refs)
        res = p.run(_target(refs, epg_refs))
        assert res.unmatched == []
        nombres = {c.name: c.matched_epg_id for c in res.channels}
        assert nombres == {"La 1": "es.la1", "A3": "es.a3"}

    def test_sin_epg_refs_y_sin_epg_legacy_no_casa(self):
        p = _pipeline({"C:/a.m3u": [Channel("A", "http://s/1", "General")]})
        p.register_merged("merged", (SourceRef("s1", "principal", "C:/a.m3u"),), ())
        res = p.run(_target((SourceRef("s1", "principal", "C:/a.m3u"),)))
        assert res.unmatched == ["A"]

    def test_canal_con_override_manual_por_url_sigue_funcionando(self):
        p = _pipeline(
            {"C:/a.m3u": [Channel("Raro", "http://a/1", "General")]},
            {"C:/g.xml": _epg("La 1", "es.la1")},
        )
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),)
        epg_refs = (SourceRef("e1", "guia", "C:/g.xml"),)
        p.register_merged("merged", refs, epg_refs)
        t = PipelineTarget(name="t", source_index="merged", source_refs=refs,
                           epg_refs=epg_refs,
                           epg_overrides={"http://a/1": "mi.id"})
        res = p.run(t)
        assert res.channels[0].matched_epg_id == "mi.id"

    def test_cada_id_epg_sabe_de_que_guia_viene(self):
        """La tabla de mapeo necesita la lista: la unión no puede perderla."""
        p = _pipeline(
            {"C:/a.m3u": [Channel("La 1", "http://a/1", "General", tvg_id="es.la1"),
                          Channel("A3", "http://a/2", "General", tvg_id="es.a3")]},
            {"C:/g1.xml": _epg("La 1", "es.la1"),
             "C:/g2.xml": _epg("Antena 3", "es.a3")},
        )
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),)
        epg_refs = (SourceRef("e1", "guia-1", "C:/g1.xml"),
                    SourceRef("e2", "guia-2", "C:/g2.xml"))
        p.register_merged("merged", refs, epg_refs)
        assert p.epg_lists("merged") == {"es.la1": ("guia-1",),
                                        "es.a3": ("guia-2",)}

    def test_id_anunciado_por_dos_guias_lista_las_dos(self):
        p = _pipeline(
            {"C:/a.m3u": [Channel("La 1", "http://a/1", "General", tvg_id="es.la1")]},
            {"C:/g1.xml": _epg("La 1", "es.la1"),
             "C:/g2.xml": _epg("La 1 HD", "es.la1")},
        )
        refs = (SourceRef("s1", "principal", "C:/a.m3u"),)
        epg_refs = (SourceRef("e1", "guia-1", "C:/g1.xml"),
                    SourceRef("e2", "guia-2", "C:/g2.xml"))
        p.register_merged("merged", refs, epg_refs)
        assert p.epg_lists("merged")["es.la1"] == ("guia-1", "guia-2")
