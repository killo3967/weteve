"""Refresco de la ventana abierta cuando config.ini cambia por fuera.

La config la escriben también el daemon y sus herramientas MCP (listas IPTV,
listas EPG, filtros, mapeos). Sin esto, la ventana abierta se queda con el
estado que leyó al arrancar.
"""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

FIXTURES = Path(__file__).parent / "fixtures" / "list_manager"


def _config(rules: str = "", overrides: str = "", sources: str = "",
            m3u_sources: str = "", custom_m3u: str = "C:/fake.m3u",
            custom_epg: str = "") -> str:
    return (
        "[listmanager.target.0]\n"
        "name = x\n"
        f"custom_m3u = {custom_m3u}\n"
        f"custom_epg = {custom_epg}\n"
        "port = 8080\n"
        "bind = 127.0.0.1\n"
        f"rules = {rules}\n"
        f"epg_overrides = {overrides}\n"
        "disabled_channels = \n"
        f"m3u_sources = {m3u_sources}\n"
        "epg_sources = \n"
        f"{sources}"
    )


@pytest.fixture
def fake_m3u(monkeypatch):
    from src.infrastructure.adapters import file_m3u_repository as repo

    monkeypatch.setattr(
        repo.FileM3URepository, "_get_content",
        lambda self, source: [
            "#EXTM3U",
            '#EXTINF:-1 group-title="Cine",Canal A', "http://st/a",
            '#EXTINF:-1 group-title="Deportes",Canal B', "http://st/b",
        ])


def _make_dialog(qtbot, path: Path, **cfg) -> ListManagerDialog:
    config = {"target_id": "0", "_config_path": path, "name": "x",
              "port": 8080, "custom_m3u": "C:/fake.m3u"}
    config.update(cfg)
    d = ListManagerDialog(None, config=config)
    qtbot.addWidget(d)
    # los tests disparan el refresco a mano: sin polling de fondo
    d._config_watch.stop()
    return d


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(_config(), encoding="utf-8")
    return p


@pytest.fixture
def dialog(qtbot, cfg_path, fake_m3u):
    return _make_dialog(qtbot, cfg_path)


class TestDeteccionDeCambio:
    def test_sin_cambios_no_recarga(self, dialog, monkeypatch):
        calls = []
        monkeypatch.setattr(dialog, "_apply_config_from_disk",
                            lambda: calls.append(1))
        dialog._reload_config_if_changed()
        assert calls == []

    def test_fichero_modificado_recarga_una_vez(self, dialog, cfg_path,
                                                monkeypatch):
        calls = []
        monkeypatch.setattr(dialog, "_apply_config_from_disk",
                            lambda: calls.append(1))
        cfg_path.write_text(_config(rules="group-title:Cine:0:and"),
                            encoding="utf-8")
        dialog._reload_config_if_changed()
        dialog._reload_config_if_changed()
        assert calls == [1]

    def test_vigilante_activo_y_vigilando_el_fichero(self, qtbot, cfg_path):
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg_path, "name": "x",
            "port": 8080, "custom_m3u": "C:/fake.m3u"})
        qtbot.addWidget(d)
        assert d._config_watch.isActive()
        assert d._config_watch.interval() > 0


class TestRefresco:
    def test_refresca_filtros(self, dialog, cfg_path):
        assert dialog.rules_widget.table.rowCount() == 0
        cfg_path.write_text(
            _config(rules="group-title:Cine:0:and|custom-filter:!{HBO}:0:and"),
            encoding="utf-8")
        dialog._reload_config_if_changed()
        assert dialog.rules_widget.table.rowCount() == 2

    def test_refresca_mapeos_epg(self, dialog, cfg_path):
        cfg_path.write_text(
            _config(overrides="http://st/a::Cine.TV"), encoding="utf-8")
        dialog._reload_config_if_changed()
        assert dialog._overrides_now() == {"http://st/a": "Cine.TV"}

    def test_refresca_listas_iptv(self, dialog, cfg_path):
        cfg_path.write_text(_config(
            sources=("[source.aaa]\nname = Otra\nkind = m3u\n"
                     "source_type = file\nlocation = C:/otra.m3u\n"
                     "refresh_min = 0\nenabled = 1\n"),
            m3u_sources="aaa"), encoding="utf-8")
        dialog._reload_config_if_changed()
        assert dialog.catalog_m3u_widget.table.rowCount() == 1
        assert dialog.catalog_m3u_widget.table.item(0, 0).text() == "Otra"
        refs = dialog._catalog_refs(dialog.catalog_m3u_widget, "m3u")
        assert [r.name for r in refs] == ["Otra"]

    def test_refresca_listas_epg(self, dialog, cfg_path):
        cfg_path.write_text(_config(
            sources=("[source.bbb]\nname = Guia\nkind = epg\n"
                     "source_type = file\nlocation = C:/guia.xml\n"
                     "refresh_min = 0\nenabled = 1\n"),
            m3u_sources=""), encoding="utf-8")
        dialog._reload_config_if_changed()
        assert dialog.catalog_epg_widget.table.rowCount() == 1
        assert dialog.catalog_epg_widget.table.item(0, 0).text() == "Guia"

    def test_refresca_tabla_de_mapeo(self, dialog, fake_api, cfg_path):
        """El mapeo externo (MCP) aparece en la tabla y en el target a guardar."""
        fake_api.served_channels = [{
            "name": "Canal Cine Plus",
            "url": "http://stream.example.com/cineplus",
            "group": "Cine", "tvg_id": "", "logo_url": "", "disabled": False}]
        fake_api.epg = [{"id": "es.la1", "display_name": "La 1"}]
        dialog._refresh_mapping()
        assert dialog._overrides_now() == {}

        cfg_path.write_text(_config(
            overrides="http://stream.example.com/cineplus::es.la1"),
            encoding="utf-8")
        dialog._reload_config_if_changed()

        # el daemon ya no lo lista como pendiente: se sirve con su guía
        fake_api.served_channels = []
        dialog._refresh_mapping()
        assert dialog._overrides_now() == {
            "http://stream.example.com/cineplus": "es.la1"}

    def test_refresca_vista_previa_de_filtros(self, dialog, fake_api, cfg_path):
        fake_api.preview_names = ["Canal A", "Canal B"]
        fake_api.preview_total = 2
        dialog._refresh_filter_preview()
        assert dialog.preview_list.count() == 2

        cfg_path.write_text(_config(rules="group-title:Cine:0:or"),
                            encoding="utf-8")
        fake_api.preview_names = ["Canal A"]
        fake_api.preview_total = 2
        dialog._reload_config_if_changed()
        assert dialog.preview_list.count() == 1
        assert fake_api.calls[-1][0] == "preview"
        assert "Cine" in fake_api.calls[-1][1][0]
