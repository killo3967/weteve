"""Cartera de fuentes en ``daemon.toml``: round-trip y poda de refs.

La cartera vive en los bloques ``[[sources]]``. El lector del INI viejo
(``read_catalog``) se prueba a través de la importación de ``config_toml``.
"""


from toml_helpers import source, target, write_daemon

from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
)
from src.infrastructure.client import config_toml
from src.infrastructure.client.sources_store import load_catalog, save_catalog


def _entry(**kw) -> SourceEntry:
    base = dict(id="abc123", name="principal", kind="m3u", source_type="file",
                location="C:/listas/principal.m3u", refresh_min=60,
                enabled=True)
    base.update(kw)
    return SourceEntry(**base)


class TestRoundTrip:
    def test_sin_fichero_la_cartera_es_vacia(self, tmp_path):
        assert load_catalog(tmp_path / "daemon.toml").all() == []

    def test_save_y_load_conservan_todos_los_campos(self, tmp_path):
        cfg = write_daemon(tmp_path / "daemon.toml")
        cat = SourceCatalog([
            _entry(id="a", name="principal", refresh_min=60),
            _entry(id="b", name="guias", kind="epg", source_type="url",
                   location="http://e/x.xml.gz", refresh_min=0, enabled=False),
        ])
        save_catalog(cfg, cat)
        got = load_catalog(cfg)
        assert [e.id for e in got.all()] == ["a", "b"]
        assert got.get("a").refresh_min == 60
        assert got.get("b").kind == "epg"
        assert got.get("b").source_type == "url"
        assert got.get("b").location == "http://e/x.xml.gz"
        assert got.get("b").enabled is False

    def test_nombre_con_barra_vertical_se_recupera_igual(self, tmp_path):
        """Los nombres de lista reales llevan '|' ('Series | HBO')."""
        cfg = write_daemon(tmp_path / "daemon.toml")
        save_catalog(cfg, SourceCatalog([_entry(id="a", name="Series | HBO | 4K")]))
        assert load_catalog(cfg).get("a").name == "Series | HBO | 4K"

    def test_ubicacion_con_espacios_y_unicode(self, tmp_path):
        cfg = write_daemon(tmp_path / "daemon.toml")
        loc = "C:/listas/Lista de ejemplo.m3u"
        save_catalog(cfg, SourceCatalog([_entry(id="a", location=loc)]))
        assert load_catalog(cfg).get("a").location == loc

    def test_eliminar_de_la_cartera_la_borra_el_fichero(self, tmp_path):
        cfg = write_daemon(tmp_path / "daemon.toml")
        save_catalog(cfg, SourceCatalog([_entry(id="a"), _entry(id="b")]))
        save_catalog(cfg, SourceCatalog([_entry(id="a")]))
        assert [e.id for e in load_catalog(cfg).all()] == ["a"]

    def test_guardar_la_cartera_poda_las_refs_huerfanas(self, tmp_path):
        """Si una fuente desaparece, los targets no pueden apuntar a un id muerto."""
        cfg = write_daemon(
            tmp_path / "daemon.toml",
            sources=[source("a", "uno", "m3u", "C:/a.m3u"),
                     source("b", "dos", "m3u", "C:/b.m3u")],
            targets=[target("0", m3u_sources=["a", "b"])])

        save_catalog(cfg, SourceCatalog([_entry(id="a", location="C:/a.m3u")]))

        raw = config_toml.load(cfg)["targets"][0]
        assert raw["m3u_sources"] == ["a"]

    def test_las_fuentes_sin_id_se_ignoran(self, tmp_path):
        cfg = tmp_path / "daemon.toml"
        cfg.write_text(
            'version = 2\n\n[[sources]]\nname = "sin id"\nkind = "m3u"\n'
            'location = "C:/x.m3u"\n', encoding="utf-8")
        assert load_catalog(cfg).all() == []
