"""Tabla de mapeo: la GUI pinta lo que le da el daemon.

El invariante "los canales que necesitan atención siguen listados" (incluidos
los descartados sin guía y los desactivados) vive ahora en
``TargetSupervisor.mapping_candidates``; aquí se comprueba que el diálogo lo
pinta, con su catálogo de guía y su resumen.
"""

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "custom_m3u": "C:/lista.m3u", "rules": [], "port": 8080})
    qtbot.addWidget(d)
    return d


def _row(name="Antena 3 Internacional", url="http://x/a3int.ts",
         tvg_id="", disabled=False, logo=""):
    return {"name": name, "url": url, "group": "G", "tvg_id": tvg_id,
            "logo_url": logo, "disabled": disabled}


def _rows(dialog):
    table = dialog.epg_mapping.table
    return [table.item(r, 0).text() for r in range(table.rowCount())]


class TestFilasDeLaTabla:
    def test_las_filas_son_los_candidatos_del_daemon(self, dialog, fake_api):
        fake_api.served_channels = [_row()]
        dialog._refresh_mapping()
        assert _rows(dialog) == ["Antena 3 Internacional"]

    def test_un_desactivado_sigue_listado(self, dialog, fake_api):
        fake_api.served_channels = [_row(name="La 1", url="http://x/la1.ts",
                                         disabled=True)]
        dialog._refresh_mapping()
        assert _rows(dialog) == ["La 1"]
        assert dialog.epg_mapping.get_disabled() == ("http://x/la1.ts",)

    def test_el_catalogo_de_guia_llena_el_desplegable(self, dialog, fake_api):
        fake_api.served_channels = [_row()]
        fake_api.epg = [{"id": "es.la1", "display_name": "La 1"}]
        dialog._refresh_mapping()
        combo = dialog.epg_mapping.table.cellWidget(0, dialog.epg_mapping.COL_EPG)
        assert "La 1" in [combo._combo.itemText(i)
                          for i in range(combo._combo.count())]

    def test_la_url_del_logo_llega_a_la_fila(self, dialog, fake_api):
        fake_api.served_channels = [_row(logo="http://x/logo.png")]
        dialog._refresh_mapping()
        item = dialog.epg_mapping.table.item(0, dialog.epg_mapping.COL_LOGO)
        assert item.data(0x0100) == "http://x/logo.png"  # UserRole


class TestResumen:
    def test_cuenta_servidos_y_pendientes(self, dialog, fake_api):
        fake_api.served = 5
        fake_api.served_channels = [_row(), _row(name="Otra", url="http://y")]
        dialog._refresh_mapping()
        text = dialog.result_label.text()
        assert "5 canales servidos" in text
        assert "2 necesitan atención" in text

    def test_nada_pendiente(self, dialog, fake_api):
        dialog._refresh_mapping()
        assert "ninguno necesita atención" in dialog.result_label.text()


class TestSinDaemon:
    def test_tabla_vacia_y_aviso(self, dialog, fake_api):
        fake_api.down = True
        dialog._refresh_mapping()
        assert dialog.epg_mapping.table.rowCount() == 0
        assert "no responde" in dialog.result_label.text()
