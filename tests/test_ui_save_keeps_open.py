"""Guardar persiste la config SIN cerrar el diálogo."""
import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog
from src.application.services.list_manager.models import FilterRule
from pathlib import Path
import tempfile


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\nname = x\nport = 8080\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "rules": [FilterRule.parse("group-title:Spain:0")], "port": 8080,
    })
    qtbot.addWidget(d)
    return d


class TestGuardarSinCerrar:
    def test_click_guardar_no_cierra(self, dialog):
        from PyQt6.QtWidgets import QDialogButtonBox
        bb = dialog.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        assert dialog.result() != 1  # no aceptó: sigue abierta

    def test_click_guardar_persiste(self, dialog, tmp_path):
        from PyQt6.QtWidgets import QDialogButtonBox
        dialog.name_edit.setText("nuevo-nombre")
        bb = dialog.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        texto = (tmp_path / "config.ini").read_text(encoding="utf-8")
        assert "nuevo-nombre" in texto

    def test_close_sigue_cerrando(self, dialog):
        from PyQt6.QtWidgets import QDialogButtonBox
        bb = dialog.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Close).click()
        assert dialog.result() == 0
