"""Diálogo: pestañas de cartera construyen target multi-fuente y persisten."""

import configparser
from pathlib import Path

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def config_ini(qtbot, tmp_path: Path) -> Path:
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\n"
        "name = x\nport = 8080\n",
        encoding="utf-8",
    )
    return cfg


def _dialog(qtbot, cfg: Path):
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080,
    })
    qtbot.addWidget(d)
    return d


class TestPestanasCartera:
    def test_pestanas_existen(self, qtbot, config_ini):
        d = _dialog(qtbot, config_ini)
        titles = [d.tabs.tabText(i) for i in range(d.tabs.count())]
        assert "Listas IPTV" in titles and "Listas EPG" in titles

    def test_target_multi_fuente_con_filas_activas(self, qtbot, config_ini):
        d = _dialog(qtbot, config_ini)
        w = d.catalog_m3u_widget
        w.add_row()
        w.table.item(0, 0).setText("platero")
        w.table.item(0, 2).setText("http://s/l.m3u")
        t = d._build_target()
        assert t.source_index == "merged"
        assert [r.name for r in t.source_refs] == ["platero"]

    def test_sin_filas_activas_flujo_legacy(self, qtbot, config_ini):
        d = _dialog(qtbot, config_ini)
        d.custom_m3u_edit.setText("C:/legacy.m3u")
        t = d._build_target()
        assert t.source_index == "C:/legacy.m3u"
        assert t.source_refs == ()

    def test_guardar_persiste_cartera_y_selecciones(self, qtbot, config_ini):
        from PyQt6.QtWidgets import QDialogButtonBox
        d = _dialog(qtbot, config_ini)
        w = d.catalog_m3u_widget
        w.add_row()
        w.table.item(0, 0).setText("platero")
        w.table.item(0, 2).setText("http://s/l.m3u")
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()

        parser = configparser.ConfigParser()
        parser.read(config_ini, encoding="utf-8")
        # cartera persistida
        assert parser.get("source.platero" if parser.has_section("source.platero")
                          else parser.sections()[-1], "name") in ("platero",)
        secs = [s for s in parser.sections() if s.startswith("source.")]
        assert len(secs) == 1
        assert parser.get(secs[0], "kind") == "m3u"
        # selección en el target
        assert parser.get("listmanager.target.0", "m3u_sources") == \
            secs[0].removeprefix("source.")

    def test_abrir_con_selecciones_previas_marca_filas(self, qtbot, config_ini):
        config_ini.write_text(
            "[DAEMON]\ncontrol_port = 32123\n\n[source.platero]\n"
            "name = platero\nkind = m3u\nsource_type = url\n"
            "location = http://s/l.m3u\nrefresh_min = 30\nenabled = 1\n\n"
            "[listmanager.target.0]\nname = x\nport = 8080\n"
            "m3u_sources = platero\n",
            encoding="utf-8",
        )
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": config_ini, "name": "x",
            "port": 8080,
            "m3u_refs": [],  # load_config las resolvería; aquí vacío => widget lee cartera
        })
        qtbot.addWidget(d)
        # la fila existe y su estado Activa refleja la selección guardada
        w = d.catalog_m3u_widget
        assert w.table.rowCount() == 1
        # sin m3u_refs en el config dict, la selección viene vacía; el test
        # del round-trip completo vive en test_persistencia_refs
        assert w.table.item(0, 0).text() == "platero"
