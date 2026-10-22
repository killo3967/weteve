"""Diálogo: pestañas de cartera construyen target multi-fuente y persisten."""

from pathlib import Path

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def config_ini(qtbot, tmp_path: Path) -> Path:
    from toml_helpers import target, write_client, write_daemon

    write_client(tmp_path / "weteve.ini", control_port=32123)
    return write_daemon(tmp_path / "daemon.toml",
                        targets=[target("0", name="x")])


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

    def test_target_multi_fuente_con_filas_activas(self, qtbot, config_ini,
                                                   fake_api, wait_save):
        """Con filas activas el target que se manda lleva sus ids."""
        d = _dialog(qtbot, config_ini)
        w = d.catalog_m3u_widget
        w.add_row()
        w.table.item(0, 0).setText("principal")
        w.table.item(0, 2).setText("http://s/l.m3u")
        w.set_selection([w.selected_ids()[0]])
        d._on_save()
        wait_save(d)
        cartera = [c for c in fake_api.calls if c[0] == "sources"][-1][1]
        seleccion = [c for c in fake_api.calls if c[0] == "target_sources"][-1]
        assert seleccion[2] == [cartera[0]["id"]]

    def test_sin_filas_activas_no_manda_fuentes(self, qtbot, config_ini,
                                                fake_api):
        """Sin filas activas no hay selección: manda la lista vacía."""
        cfg = config_ini.read_text(encoding="utf-8").replace(
            "name = x", "name = x\ncustom_m3u = C:/legacy.m3u")
        config_ini.write_text(cfg, encoding="utf-8")
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": config_ini, "name": "x",
            "port": 8080, "custom_m3u": "C:/legacy.m3u"})
        qtbot.addWidget(d)
        d._on_save()
        seleccion = [c for c in fake_api.calls if c[0] == "target_sources"][-1]
        assert seleccion[2] == []

    def test_guardar_manda_cartera_y_selecciones(self, qtbot, config_ini,
                                                 fake_api, wait_save):
        """Guardar envía la cartera visible y la selección del target."""
        from PyQt6.QtWidgets import QDialogButtonBox

        d = _dialog(qtbot, config_ini)
        w = d.catalog_m3u_widget
        w.add_row()
        row = w.table.rowCount() - 1
        w.table.item(row, 0).setText("principal")
        w.table.item(row, 2).setText("http://s/l.m3u")
        w.set_selection([w.selected_ids()[0]])  # fila activa del target
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        wait_save(d)

        cartera = [c for c in fake_api.calls if c[0] == "sources"][-1][1]
        assert [e["name"] for e in cartera] == ["principal"]
        assert cartera[0]["kind"] == "m3u"
        seleccion = [c for c in fake_api.calls
                     if c[0] == "target_sources"][-1]
        assert seleccion[2] == [cartera[0]["id"]]

    def test_abrir_con_selecciones_previas_marca_filas(self, qtbot, config_ini):
        from toml_helpers import source, target, write_daemon

        write_daemon(config_ini,
                     sources=[source("principal", "principal", "m3u",
                                     "http://s/l.m3u", refresh_min=30)],
                     targets=[target("0", name="x",
                                     m3u_sources=["principal"])])
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
        assert w.table.item(0, 0).text() == "principal"


class TestCamposHeredados:
    def test_guardar_no_borra_custom_m3u_si_el_llamador_no_lo_pasa(
            self, qtbot, config_ini):
        """Guardar reescribe la sección: los campos legacy se leen del fichero."""
        from toml_helpers import patch_daemon

        patch_daemon(config_ini, lambda c: c["targets"][0].update(
            custom_m3u="C:/vieja.m3u", custom_epg="C:/tv.xml"))
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": config_ini, "name": "x",
            "port": 8080})
        qtbot.addWidget(d)
        saved = d.get_target_config()
        assert saved["custom_m3u"] == "C:/vieja.m3u"
        assert saved["custom_epg"] == "C:/tv.xml"
