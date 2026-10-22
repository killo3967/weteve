"""Modelo multi-fuente: SourceRef en el target, source_name en el canal,
prefijo 'Lista.Canal' solo al renderizar (filtros y EPG ven el nombre limpio).
"""

from datetime import datetime, timedelta

from src.application.services.list_manager.models import (
    FilterRule,
    MappedChannel,
    PipelineTarget,
    SourceRef,
)
from src.application.services.list_manager.playlist_builder import build_m3u, build_xmltv
from src.domain.entities.epg import EPGData, Program


class TestSourceRef:
    def test_ref_con_identificador_y_nombre(self):
        r = SourceRef(id="abc123", name="principal")
        assert r.id == "abc123" and r.name == "principal"

    def test_target_puede_referenciar_varias_fuentes(self):
        t = PipelineTarget(
            name="t", source_index="merged",
            source_refs=(SourceRef("a", "principal"), SourceRef("b", "secunda")),
            epg_refs=(SourceRef("e", "TV"),),
        )
        assert [r.id for r in t.source_refs] == ["a", "b"]
        assert [r.name for r in t.epg_refs] == ["TV"]

    def test_targets_antiguos_sin_refs_siguen_construyendose(self):
        """Regresión: la firma vieja (source_index único) debe seguir valiendo."""
        t = PipelineTarget(name="t", source_index="C:/x.m3u",
                           rules=(FilterRule.parse("group-title:Spain:0"),))
        assert t.source_refs == ()
        assert t.epg_refs == ()


class TestMappedChannelSource:
    def test_source_name_por_defecto_vacio(self):
        ch = MappedChannel(name="La 1", url="http://a")
        assert ch.source_name == ""

    def test_la_fuente_no_se_antepone_al_nombre(self):
        """El nombre de salida es el del canal: sin '<lista>.' delante."""
        ch = MappedChannel(name="La 1", url="http://a", source_name="principal")
        assert ch.name == "La 1"
        assert not hasattr(ch, "display_name")

    def test_name_interno_sigue_limpiopara_filtros_y_epg(self):
        """El nombre canónico es el del canal y no lleva la lista delante."""
        ch = MappedChannel(name="La 1", url="http://a", source_name="principal")
        assert ch.name == "La 1"
        assert ch.url == "http://a"


class TestRenderNombreDeCanal:
    def test_build_m3u_no_prefija_con_el_nombre_de_lista(self):
        chans = [
            MappedChannel(name="La 1", url="http://a", group="Spain",
                          source_name="principal", chno=1),
            MappedChannel(name="La 1", url="http://b", group="Spain",
                          source_name="otra", chno=2),
        ]
        out = build_m3u(chans)
        extinf = [l for l in out.splitlines() if l.startswith("#EXTINF")]
        assert len(extinf) == 2
        assert all(l.endswith(",La 1") for l in extinf)
        assert "principal" not in out and "otra" not in out
        assert out.count("http://") == 2

    def test_build_m3u_lleva_group_title(self):
        out = build_m3u([MappedChannel(name="La 1", url="http://a",
                                       group="SPAIN", chno=1)])
        assert 'group-title="SPAIN"' in out

    def test_build_m3u_sin_grupo_usa_otros(self):
        out = build_m3u([MappedChannel(name="La 1", url="http://a", chno=1)])
        assert 'group-title="Otros"' in out

    def test_los_atributos_no_llevan_prefijo(self):
        """La lista de origen nunca aparece en la línea de salida."""
        ch = MappedChannel(name="HBO", url="http://h", source_name="principal",
                           tvg_id="hbo.id", chno=1)
        out = build_m3u([ch])
        extinf = next(l for l in out.splitlines() if l.startswith("#EXTINF"))
        assert extinf.rstrip().endswith(",HBO")
        # el id EPG viaja en el atributo; el nombre de la lista, nunca
        assert 'tvg-id="hbo.id"' in extinf
        assert "principal" not in extinf
        assert "principal" not in ch.url

    def test_xmltv_display_name_es_el_del_canal(self):
        ch = MappedChannel(name="La 1", url="http://a", source_name="principal",
                           matched_epg_id="la1.id")
        now = datetime(2026, 1, 1, 12)
        epg = EPGData(
            programs=[Program("Telediario", now, now + timedelta(hours=1), "la1.id")],
            channel_names={"la1.id": "La 1"},
        )
        out = build_xmltv([ch], epg)
        assert "<display-name>La 1</display-name>" in out
        assert "principal" not in out


class TestRefsConLocation:
    """El ref copia location: el pipeline no necesita releer la cartera."""

    def test_ref_con_location(self):
        r = SourceRef("a", "principal", "C:/x.m3u")
        assert r.location == "C:/x.m3u"

    def test_location_por_defecto_vacia(self):
        assert SourceRef("a", "principal").location == ""
