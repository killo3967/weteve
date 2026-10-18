"""Guardar manda el lote a la API y NO cierra el diálogo.

La GUI es un frontend: el fichero lo escribe el demonio. Aquí se comprueba que
Guardar no cierra la ventana, que el lote sale completo con recargas diferidas y
que la ventana no reescribe el target por su cuenta.
"""

import pytest

from src.application.services.list_manager.models import FilterRule
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\n"
        "name = x\nport = 8080\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "rules": [FilterRule.parse("group-title:Spain:0")], "port": 8080})
    qtbot.addWidget(d)
    d._config_watch.stop()
    return d


def _click_save(dialog) -> None:
    from PyQt6.QtWidgets import QDialogButtonBox
    bb = dialog.findChildren(QDialogButtonBox)[0]
    bb.button(QDialogButtonBox.StandardButton.Save).click()


class TestGuardarSinCerrar:
    def test_click_guardar_no_cierra(self, dialog, wait_save):
        from PyQt6.QtWidgets import QDialogButtonBox
        bb = dialog.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        wait_save(dialog)
        assert dialog.result() != 1  # no aceptó: sigue abierta

    def test_click_guardar_manda_el_lote(self, dialog, fake_api, wait_save):
        dialog.name_edit.setText("nuevo-nombre")
        _click_save(dialog)
        wait_save(dialog)
        tipos = [c[0] for c in fake_api.calls]
        assert "sources" in tipos
        assert tipos.index("sources") < tipos.index("target_sources")
        assert tipos.count("run") == 1

        puts = {c[1]: c[2] for c in fake_api.calls if c[0] == "put"}
        assert puts["/targets/0/settings"]["name"] == "nuevo-nombre"
        assert puts["/targets/0/settings"]["reload"] is False
        assert puts["/targets/0/rules"]["reload"] is False
        assert puts["/targets/0/mappings"]["reload"] is False
        assert puts["/targets/0/disabled"]["reload"] is False
        # las fuentes del target tampoco recargan: solo el `run` final ejecuta
        # el pipeline (si recarga aquí, el guardado se pasa del timeout)
        fuentes = [c for c in fake_api.calls if c[0] == "target_sources"][-1]
        assert fuentes[4] is False
        assert "Guardado" in dialog.result_label.text()

    def test_el_dialogo_no_reescribe_el_target(self, dialog, fake_api,
                                               wait_save):
        antes = dialog._config["_config_path"].read_text(encoding="utf-8")
        dialog.name_edit.setText("nuevo-nombre")
        _click_save(dialog)
        wait_save(dialog)
        despues = dialog._config["_config_path"].read_text(encoding="utf-8")
        assert "name = x" in despues          # el target lo escribe el demonio
        assert "descripción" not in despues
        assert "name = x" in antes

    def test_close_sigue_cerrando(self, dialog):
        from PyQt6.QtWidgets import QDialogButtonBox
        bb = dialog.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Close).click()
        assert dialog.result() == 0
