"""Pruebas de la persistencia de la cartera de fuentes y su migracion.

Cubre el round-trip [SOURCES] y la migracion de configs antiguas donde cada
target tenia su unica lista en custom_m3u / custom_epg.
"""

import configparser
from pathlib import Path

from src.application.services.list_manager.source_catalog import SourceCatalog, SourceEntry
from src.infrastructure.client.sources_store import (
    load_catalog,
    migrate_legacy_sources,
    save_catalog,
)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _entry(**kw):
    base = dict(id="abc123", name="platero", kind="m3u", source_type="file",
                location="C:/listas/platero.m3u", refresh_min=60, enabled=True)
    base.update(kw)
    return SourceEntry(**base)


class TestRoundTrip:
    def test_sin_seccion_sources_la_cartera_es_vacia(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "[SETTINGS]\nx = 1\n")
        assert load_catalog(cfg).all() == []

    def test_save_y_load_conservan_todos_los_campos(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "[SETTINGS]\nx = 1\n")
        cat = SourceCatalog([
            _entry(id="a", name="platero", refresh_min=60),
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

    def test_se_preservan_las_demas_secciones(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "[DAEMON]\nport = 8081\nmcp_port = 9000\n")
        save_catalog(cfg, SourceCatalog([_entry(id="a")]))
        parser = configparser.ConfigParser()
        parser.read(cfg, encoding="utf-8")
        assert parser.getint("DAEMON", "port") == 8081
        assert parser.has_section("source.a")

    def test_nombre_con_barra_vertical_se_recupera_igual(self, tmp_path):
        """Los nombres de lista reales llevan '|' ('Series | HBO')."""
        cfg = tmp_path / "config.ini"
        _write(cfg, "")
        save_catalog(cfg, SourceCatalog([_entry(id="a", name="Series | HBO | 4K")]))
        assert load_catalog(cfg).get("a").name == "Series | HBO | 4K"

    def test_ubicacion_con_espacios_y_unicode(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "")
        loc = "C:/Users/tomas/Downloads/Lista España 2026.m3u"
        save_catalog(cfg, SourceCatalog([_entry(id="a", location=loc)]))
        assert load_catalog(cfg).get("a").location == loc

    def test_entradas_malformadas_se_ignoran_sin_romper(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg,
            "[source.rota]\nname = sin ubicacion\nkind = m3u\nsource_type = file\n"
            "[source.rara]\nname = x\nkind = desconocido\nsource_type = file\n"
            "location = C:/z.m3u\n"
            "[source.buena]\nname = ok\nkind = m3u\nsource_type = file\n"
            "location = C:/x.m3u\n")
        cat = load_catalog(cfg)
        assert [e.id for e in cat.all()] == ["buena"]

    def test_refresco_no_numerico_se_toma_como_estatica(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "[source.a]\nname = n\nkind = m3u\nsource_type = file\n"
                    "location = C:/x.m3u\nrefresh_min = mucho\n")
        assert load_catalog(cfg).get("a").refresh_min == 0

    def test_eliminar_de_la_cartera_borra_la_seccion_vieja(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "")
        save_catalog(cfg, SourceCatalog([_entry(id="a"), _entry(id="b")]))
        save_catalog(cfg, SourceCatalog([_entry(id="a")]))
        assert [e.id for e in load_catalog(cfg).all()] == ["a"]


class TestMigracion:
    def _legacy(self, tmp_path, m3u="C:/listas/platero.m3u", epg="C:/listas/epg.xml"):
        cfg = tmp_path / "config.ini"
        _write(cfg,
            "[listmanager.target.0]\n"
            "name = target 1\n"
            f"custom_m3u = {m3u}\n"
            f"custom_epg = {epg}\n"
            "rules = group-title:Spain:0\n"
            "port = 8080\n"
        )
        return cfg

    def test_un_target_anticuo_produce_dos_entradas_y_sus_selecciones(self, tmp_path):
        cfg = self._legacy(tmp_path)
        cat = migrate_legacy_sources(cfg)
        assert len(cat) == 2
        parser = configparser.ConfigParser()
        parser.read(cfg, encoding="utf-8")
        sec = "listmanager.target.0"
        m3u_ids = parser.get(sec, "m3u_sources").split(",")
        epg_ids = parser.get(sec, "epg_sources").split(",")
        assert [cat.get(i).kind for i in m3u_ids] == ["m3u"]
        assert [cat.get(i).kind for i in epg_ids] == ["epg"]

    def test_dos_targets_con_la_misma_lista_comparten_una_entrada(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg,
            "[listmanager.target.0]\ncustom_m3u = C:/x.m3u\n"
            "[listmanager.target.1]\ncustom_m3u = C:/x.m3u\n")
        cat = migrate_legacy_sources(cfg)
        assert len(cat) == 1
        parser = configparser.ConfigParser()
        parser.read(cfg, encoding="utf-8")
        assert (parser.get("listmanager.target.0", "m3u_sources")
                == parser.get("listmanager.target.1", "m3u_sources"))

    def test_url_se_migra_como_tipo_url(self, tmp_path):
        cfg = self._legacy(tmp_path, m3u="http://servidor:80/lista.m3u")
        cat = migrate_legacy_sources(cfg)
        entrada = [e for e in cat.all() if e.kind == "m3u"][0]
        assert entrada.source_type == "url"
        assert entrada.location == "http://servidor:80/lista.m3u"

    def test_el_nombre_deriva_del_fichero(self, tmp_path):
        cfg = self._legacy(tmp_path, m3u="C:/listas/platero2026.m3u")
        cat = migrate_legacy_sources(cfg)
        entrada = [e for e in cat.all() if e.kind == "m3u"][0]
        assert entrada.name == "platero2026"

    def test_migracion_idempotente(self, tmp_path):
        cfg = self._legacy(tmp_path)
        primera = migrate_legacy_sources(cfg)
        segunda = migrate_legacy_sources(cfg)
        assert len(segunda) == len(primera) == 2
        assert [e.id for e in segunda.all()] == [e.id for e in primera.all()]

    def test_migracion_conserva_reglas_y_puerto(self, tmp_path):
        cfg = self._legacy(tmp_path)
        migrate_legacy_sources(cfg)
        parser = configparser.ConfigParser()
        parser.read(cfg, encoding="utf-8")
        sec = "listmanager.target.0"
        assert parser.get(sec, "rules") == "group-title:Spain:0"
        assert parser.getint(sec, "port") == 8080
        assert parser.get(sec, "custom_m3u") == "C:/listas/platero.m3u"

    def test_target_sin_listas_no_crea_entradas(self, tmp_path):
        cfg = tmp_path / "config.ini"
        _write(cfg, "[listmanager.target.0]\nname = vacio\nport = 8080\n")
        cat = migrate_legacy_sources(cfg)
        assert cat.all() == []

    def test_custom_epg_vacio_no_genera_entrada_epg(self, tmp_path):
        cfg = self._legacy(tmp_path, epg="")
        cat = migrate_legacy_sources(cfg)
        assert [e.kind for e in cat.all()] == ["m3u"]
