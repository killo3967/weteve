"""Un guardado en vuelo no puede hablarle a una ventana ya destruida.

``closeEvent`` no espera a un guardado en marcha (la ventana no debe
retenerte), así que el callback del ``Future`` puede llegar después de que
Qt haya destruido el diálogo. Emitir ahí lanzaba ``RuntimeError: wrapped C/C++
object of type ListManagerDialog has been deleted`` y ``concurrent.futures`` lo
apuntaba como error propio en el log.
"""

import pytest
from PyQt6 import sip

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


class _Future:
    """Doble mínimo de ``concurrent.futures.Future``."""

    def __init__(self, result=None, cancelado: bool = False):
        self._result = result
        self._cancelado = cancelado

    def cancelled(self) -> bool:
        return self._cancelado

    def result(self):
        return self._result


def _dialog(config_path) -> ListManagerDialog:
    return ListManagerDialog(None, config={
        "target_id": "0", "_config_path": config_path, "name": "x", "port": 8080,
    })


@pytest.fixture()
def cfg_file(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[listmanager.target.0]\nname = x\ncustom_m3u = /x.m3u\nport = 8080\n",
        encoding="utf-8")
    return cfg


class TestCallbackDelGuardado:
    def test_ventana_destruida_no_lanza_ni_emite(self, qapp, cfg_file) -> None:
        dialog = _dialog(cfg_file)
        dialog._config_watch.stop()
        emitidos: list = []
        dialog.save_finished.connect(emitidos.append)
        sip.delete(dialog)
        assert sip.isdeleted(dialog)
        # reproducía el fallo: el callback llegaba con la ventana ya borrada
        dialog._on_save_done(_Future({"ok": True}))
        assert emitidos == []

    def test_ventana_viva_emite_el_resultado(self, qtbot, cfg_file) -> None:
        dialog = _dialog(cfg_file)
        qtbot.addWidget(dialog)
        dialog._config_watch.stop()
        emitidos: list = []
        dialog.save_finished.connect(emitidos.append)
        dialog._on_save_done(_Future({"ok": True, "served": 3}))
        assert emitidos == [{"ok": True, "served": 3}]

    def test_futuro_cancelado_no_emite(self, qtbot, cfg_file) -> None:
        dialog = _dialog(cfg_file)
        qtbot.addWidget(dialog)
        dialog._config_watch.stop()
        emitidos: list = []
        dialog.save_finished.connect(emitidos.append)
        dialog._on_save_done(_Future({"ok": True}, cancelado=True))
        assert emitidos == []

    def test_un_fallo_del_trabajo_se_entrega_como_error(self, qtbot, cfg_file,
                                                      monkeypatch) -> None:
        class _Boom(_Future):
            def result(self):
                raise RuntimeError("fallo del job")

        dialog = _dialog(cfg_file)
        qtbot.addWidget(dialog)
        dialog._config_watch.stop()
        # el aviso de error es un QMessageBox modal: aquí solo interesa que el
        # resultado llegue con la forma que espera el manejador
        avisos: list = []
        monkeypatch.setattr(ListManagerDialog, "_save_error",
                            lambda self, body, code, label: avisos.append(
                                (body, code, label)))
        dialog._on_save_done(_Boom())
        assert avisos and avisos[0][0]["error"] == "fallo del job"
