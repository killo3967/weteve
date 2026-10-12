"""Guardar no debe bloquear la UI esperando al demonio.

notify_daemon hace ping HTTP con timeout de 5 s; en el hilo de la UI
congela el diálogo cuando el demonio no está. La notificación es
best-effort: se ejecuta en segundo plano y el mensaje de resultado se
actualiza al terminar.
"""

import time

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\n"
        "name = x\nport = 8080\n",
        encoding="utf-8",
    )
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080,
    })
    qtbot.addWidget(d)
    return d


def _save(dialog):
    from PyQt6.QtWidgets import QDialogButtonBox
    bb = dialog.findChildren(QDialogButtonBox)[0]
    bb.button(QDialogButtonBox.StandardButton.Save).click()


class TestGuardarSinBloquear:
    def test_guardar_no_llama_notify_daemon_en_el_hilo_de_ui(
            self, dialog, monkeypatch):
        """notify_daemon debe ejecutarse fuera del hilo de la UI."""
        calls = []

        def slow_notify():
            calls.append(("thread", __import__("threading").current_thread()))
            time.sleep(0.05)
            return False

        monkeypatch.setattr(dialog.daemon_panel, "notify_daemon", slow_notify)
        _save(dialog)
        # click() retorna sin haber esperado al notify (o ya terminó);
        # en cualquier caso el notify no corrió en el hilo principal
        main = __import__("threading").current_thread()
        if calls:
            assert calls[0][1] is not main

    def test_guardar_actualiza_mensaje_tras_notificar(
            self, dialog, qtbot, monkeypatch):
        monkeypatch.setattr(dialog.daemon_panel, "notify_daemon",
                            lambda: True)
        _save(dialog)
        qtbot.waitUntil(
            lambda: "aviso" in dialog.result_label.text().lower()
            or "avisado" in dialog.result_label.text().lower(), timeout=3000)
        assert "recargando" in dialog.result_label.text().lower()

    def test_guardar_persiste_igual_con_notificacion_en_segundo_plano(
            self, dialog, qtbot, monkeypatch):
        monkeypatch.setattr(dialog.daemon_panel, "notify_daemon",
                            lambda: False)
        _save(dialog)
        qtbot.waitUntil(
            lambda: "guardada" in dialog.result_label.text().lower(),
            timeout=3000)
        texto = dialog._config["_config_path"].read_text(encoding="utf-8")
        assert "name = x" in texto
