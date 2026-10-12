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
        r = SourceRef(id="abc123", name="platero")
        assert r.id == "abc123" and r.name == "platero"

    def test_target_puede_referenciar_varias_fuentes(self):
        t = PipelineTarget(
            name="t", source_index="merged",
            source_refs=(SourceRef("a", "platero"), SourceRef("b", "secunda")),
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
        assert ch.display_name == "La 1"

    def test_display_name_prefijado_cuando_hay_fuente(self):
        ch = MappedChannel(name="La 1", url="http://a", source_name="platero")
        assert ch.display_name == "platero.La 1"

    def test_name_interno_sigue_limpiopara_filtros_y_epg(self):
        """El prefijo no contamina el nombre canónico: es solo de render."""
        ch = MappedChannel(name="La 1", url="http://a", source_name="platero")
        assert ch.name == "La 1"
        assert ch.url == "http://a"


class TestRenderPrefijo:
    def test_build_m3u_prefija_con_el_nombre_de_lista(self):
        chans = [
            MappedChannel(name="La 1", url="http://a", group="Spain",
                          source_name="platero", chno=1),
            MappedChannel(name="La 1", url="http://b", group="Spain",
                          source_name="otra", chno=2),
        ]
        out = build_m3u(chans)
        assert "platero.La 1" in out and "otra.La 1" in out
        # dos canales homónimos de listas distintas conviven como entries
        assert out.count(",La 1") + out.count(",platero.La 1") + out.count(",otra.La 1") == 2
        assert out.count("http://") == 2

    def test_build_m3u_sin_fuente_no_inventa_prefijo(self):
        """El flujo antiguo (una sola fuente, preview) sigue igual."""
        out = build_m3u([MappedChannel(name="La 1", url="http://a", chno=1)])
        assert ",La 1" in out
        assert ".." not in out

    def test_los_atributos_no_llevan_prefijo(self):
        """El prefijo es cosmético: va solo en el display name tras la coma."""
        ch = MappedChannel(name="HBO", url="http://h", source_name="platero",
                           tvg_id="hbo.id", chno=1)
        out = build_m3u([ch])
        extinf = next(l for l in out.splitlines() if l.startswith("#EXTINF"))
        assert extinf.rstrip().endswith(",platero.HBO")
        assert 'tvg-id="hbo.id"' in extinf
        assert "tvg-id=\"platero" not in extinf
        assert "platero" not in ch.url

    def test_xmltv_display_name_usa_el_prefijo(self):
        ch = MappedChannel(name="La 1", url="http://a", source_name="platero",
                           matched_epg_id="la1.id")
        now = datetime(2026, 1, 1, 12)
        epg = EPGData(
            programs=[Program("Telediario", now, now + timedelta(hours=1), "la1.id")],
            channel_names={"la1.id": "La 1"},
        )
        out = build_xmltv([ch], epg)
        assert "platero.La 1" in out
