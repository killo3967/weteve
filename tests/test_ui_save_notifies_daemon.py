"""Guardar escribe por la API del demonio (un solo lote + un run)."""
import pytest

from src.application.services.list_manager.models import FilterRule
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


@pytest.fixture()
def cfg_file(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[listmanager.target.0]\nname = x\n"
        "custom_m3u = /x.m3u\nrules = group-title:Spain:0\nport = 8080\n",
        encoding="utf-8")
    return cfg


class TestDialogSaveViaApi:
    @pytest.fixture()
    def dialog_with_daemon(self, qtbot, cfg_file):
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg_file, "name": "x",
            "rules": [FilterRule.parse("group-title:Spain:0")], "port": 8080,
        })
        qtbot.addWidget(d)
        d._config_watch.stop()
        return d

    def test_guardar_ejecuta_el_pipeline_una_vez(self, dialog_with_daemon,
                                                 fake_api, wait_save):
        from PyQt6.QtWidgets import QDialogButtonBox

        d = dialog_with_daemon
        d.name_edit.setText("renovada")
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        wait_save(d)
        assert [c[0] for c in fake_api.calls].count("run") == 1
        assert "Guardado" in d.result_label.text()

    def test_sin_daemon_no_se_guarda_nada(self, dialog_with_daemon, fake_api,
                                          monkeypatch, wait_save):
        from PyQt6.QtWidgets import QDialogButtonBox, QMessageBox

        avisos: list = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda *a, **k: avisos.append(a))
        d = dialog_with_daemon
        fake_api.down = True
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        wait_save(d)
        assert avisos
        assert not any(c[0] == "run" for c in fake_api.calls)
