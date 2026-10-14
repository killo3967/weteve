"""Guardar notifica al daemon: si no se le avisa sigue sirviendo filtros viejos."""
import pytest

from src.application.services.daemon.supervisor import TargetSupervisor
from src.application.services.daemon.target_store import TargetStore
from src.application.services.list_manager.models import FilterRule
from src.infrastructure.ui.daemon_panel import DaemonPanel
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


class _FakeClient:
    def __init__(self, alive=True, reload_ok=True):
        self.alive = alive
        self.reload_ok = reload_ok
        self.reload_calls: list[str] = []
        self.shutdown_calls = 0
        self.base_url = "http://127.0.0.1:34400"

    def ping(self):
        return self.alive

    def status(self):
        return None if not self.alive else {"targets": {}}

    def reload_target(self, target_id):
        self.reload_calls.append(target_id)
        return self.reload_ok

    def shutdown(self):
        self.shutdown_calls += 1
        return self.alive


@pytest.fixture()
def cfg_file(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[listmanager.target.0]\nname = x\n"
        "custom_m3u = /x.m3u\nrules = group-title:Spain:0\nport = 8080\n",
        encoding="utf-8")
    return cfg


class TestPanelNotify:
    def test_notifica_un_daemon_vivo(self, qtbot, cfg_file):
        client = _FakeClient()
        panel = DaemonPanel(client=client, target_store=TargetStore(cfg_file),
                            config_path=cfg_file)
        qtbot.addWidget(panel)
        assert panel.notify_daemon() is True
        assert client.reload_calls == ["0"]

    def test_sin_daemon_no_notifica(self, qtbot, cfg_file):
        client = _FakeClient(alive=False)
        panel = DaemonPanel(client=client, config_path=cfg_file)
        qtbot.addWidget(panel)
        assert panel.notify_daemon() is False
        assert client.reload_calls == []


class TestDialogSaveNotifies:
    @pytest.fixture()
    def dialog_with_daemon(self, qtbot, cfg_file):
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg_file, "name": "x",
            "rules": [FilterRule.parse("group-title:Spain:0")], "port": 8080,
        })
        qtbot.addWidget(d)
        client = _FakeClient()
        d.daemon_panel._client = client
        return d, client

    def test_guardar_pide_reload(self, dialog_with_daemon, qtbot):
        from PyQt6.QtWidgets import QDialogButtonBox
        d, client = dialog_with_daemon
        d.name_edit.setText("renovada")
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        # el aviso al demonio va en segundo plano: esperar su callback
        qtbot.waitUntil(lambda: client.reload_calls == ["0"], timeout=3000)
        qtbot.waitUntil(
            lambda: "demonio" in d.result_label.text().lower(), timeout=3000)

    def test_guardar_funciona_sin_daemon(self, dialog_with_daemon, qtbot):
        from PyQt6.QtWidgets import QDialogButtonBox
        d, client = dialog_with_daemon
        client.alive = False
        bb = d.findChildren(QDialogButtonBox)[0]
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        qtbot.waitUntil(
            lambda: "No se notificó al demonio" in d.result_label.text(),
            timeout=3000)
        assert client.reload_calls == []
