"""El guardado es un lote: recargas diferidas y UNA sola ejecución.

Antes cada cambio del diálogo reejecutaba el pipeline (3-8 s con listas
grandes). Ahora todas las escrituras van con ``reload: false`` y el pipeline se
ejecuta una vez al final con ``POST /run``.
"""

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

pytest.importorskip("PyQt6")


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[DAEMON]\ncontrol_port = 32123\n\n[listmanager.target.0]\n"
        "name = x\nport = 8080\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080})
    qtbot.addWidget(d)
    d._config_watch.stop()
    return d


def _save(dialog) -> None:
    from PyQt6.QtWidgets import QDialogButtonBox
    bb = dialog.findChildren(QDialogButtonBox)[0]
    bb.button(QDialogButtonBox.StandardButton.Save).click()


class TestGuardarEnLote:
    def test_una_sola_ejecucion_del_pipeline(self, dialog, fake_api,
                                             wait_save):
        _save(dialog)
        wait_save(dialog)
        assert [c[0] for c in fake_api.calls].count("run") == 1

    def test_todas_las_escrituras_difieren_la_recarga(self, dialog, fake_api,
                                                      wait_save):
        _save(dialog)
        wait_save(dialog)
        puts = [c[2] for c in fake_api.calls if c[0] == "put"]
        assert puts and all(p.get("reload") is False for p in puts)

    def test_mensaje_final_con_los_canales_servidos(self, dialog, fake_api,
                                                    wait_save):
        fake_api.served = 7
        _save(dialog)
        wait_save(dialog)
        assert "7 canales servidos" in dialog.result_label.text()

    def test_sin_demonio_avisa_y_no_escribe(self, dialog, fake_api,
                                            monkeypatch, wait_save):
        from PyQt6.QtWidgets import QMessageBox

        avisos = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda *a, **k: avisos.append(a))
        fake_api.down = True
        _save(dialog)
        wait_save(dialog)
        assert avisos
        assert not any(c[0] in ("put", "sources", "target_sources")
                       for c in fake_api.calls)


class TestGuardadoNoBloqueaLaVentana:
    """El lote HTTP y el pipeline van en un hilo: la GUI no se congela."""

    def test_guardar_retorna_antes_del_pipeline(self, dialog, fake_api,
                                                wait_save):
        import time

        from PyQt6.QtWidgets import QDialogButtonBox

        lento = fake_api.run

        def run_lento(target_id):
            time.sleep(0.4)          # simula el pipeline de varios segundos
            return lento(target_id)

        fake_api.run = run_lento
        bb = dialog.findChildren(QDialogButtonBox)[0]
        t0 = time.monotonic()
        bb.button(QDialogButtonBox.StandardButton.Save).click()
        transcurrido = time.monotonic() - t0
        assert transcurrido < 0.2                 # no esperó al pipeline
        assert dialog.save_in_progress()
        assert dialog._save_button.isEnabled() is False
        assert "Guardando" in dialog.result_label.text()

        wait_save(dialog)
        assert dialog._save_button.isEnabled() is True
        assert "Guardado" in dialog.result_label.text()

    def test_doble_click_no_encola_dos_guardados(self, dialog, fake_api,
                                                 wait_save):
        import time

        from PyQt6.QtWidgets import QDialogButtonBox

        lento = fake_api.run

        def run_lento(target_id):
            time.sleep(0.2)
            return lento(target_id)

        fake_api.run = run_lento
        bb = dialog.findChildren(QDialogButtonBox)[0]
        boton = bb.button(QDialogButtonBox.StandardButton.Save)
        boton.click()
        boton.click()
        wait_save(dialog)
        assert [c[0] for c in fake_api.calls].count("run") == 1
