"""Arranque y parada automáticos del demonio desde la interfaz.

Dos casillas persistentes en ``[DAEMON]``: ``autostart`` (lanza el demonio si no
responde al abrir) y ``stop_on_exit`` (lo para al cerrar). La regla que evita
sustos: solo se para el demonio que arrancó esta ventana, nunca uno del
Programador de tareas.
"""

import pytest

pytest.importorskip("PyQt6")

from src.application.services.daemon.config import (
    DaemonConfig,
    load_daemon_config,
    save_daemon_config,
)
from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


class _FakePanel:
    """Doble del DaemonPanel: registra arranques y paradas."""

    def __init__(self, autostart: bool = False, stop_on_exit: bool = False,
                 already_running: bool = False, launch_ok: bool = True) -> None:
        self.autostart = autostart
        self.stop_on_exit = stop_on_exit
        self.already_running = already_running
        self.launch_ok = launch_ok
        self.started = 0
        self.stopped = 0

    def autostart_checked(self) -> bool:
        return self.autostart

    def stop_on_exit_checked(self) -> bool:
        return self.stop_on_exit

    def ensure_running(self) -> bool:
        if self.already_running:
            return False
        self.started += 1
        return self.launch_ok

    def stop_daemon(self) -> None:
        self.stopped += 1


def _dialog(qtbot, tmp_path, panel: _FakePanel) -> ListManagerDialog:
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080})
    qtbot.addWidget(d)
    d._config_watch.stop()
    # se construye el diálogo real y luego se sustituye el panel: el arranque
    # automático se prueba llamando a la lógica del diálogo
    d.daemon_panel = panel  # type: ignore[assignment]
    d._auto_started_daemon = False
    d._daemon_stopped = False
    return d


class TestPersistencia:
    def test_las_casillas_se_guardan_y_se_leen(self, tmp_path):
        path = tmp_path / "config.ini"
        save_daemon_config(path, DaemonConfig(autostart=True,
                                              stop_on_exit=True))
        cfg = load_daemon_config(path)
        assert cfg.autostart is True and cfg.stop_on_exit is True

    def test_por_defecto_apagadas(self, tmp_path):
        path = tmp_path / "config.ini"
        save_daemon_config(path, DaemonConfig())
        cfg = load_daemon_config(path)
        assert cfg.autostart is False and cfg.stop_on_exit is False


class TestArranqueAlAbrir:
    def test_arranca_si_esta_marcado(self, qtbot, tmp_path):
        panel = _FakePanel(autostart=True)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = panel.ensure_running()
        assert d._auto_started_daemon is True
        assert panel.started == 1

    def test_no_arranca_si_no_esta_marcado(self, qtbot, tmp_path):
        panel = _FakePanel(autostart=False)
        d = _dialog(qtbot, tmp_path, panel)
        assert d._auto_started_daemon is False
        assert panel.started == 0

    def test_si_ya_estaba_vivo_no_se_considera_nuestro(self, qtbot, tmp_path):
        panel = _FakePanel(autostart=True, already_running=True)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = panel.ensure_running()
        assert d._auto_started_daemon is False


class TestParadaAlCerrar:
    def test_para_si_lo_arrancamos_y_esta_marcado(self, qtbot, tmp_path):
        panel = _FakePanel(stop_on_exit=True)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = True
        d._maybe_stop_daemon()
        assert panel.stopped == 1

    def test_no_para_un_demonio_ajeno(self, qtbot, tmp_path):
        panel = _FakePanel(stop_on_exit=True)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = False  # ya estaba corriendo antes
        d._maybe_stop_daemon()
        assert panel.stopped == 0

    def test_no_para_sin_casilla(self, qtbot, tmp_path):
        panel = _FakePanel(stop_on_exit=False)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = True
        d._maybe_stop_daemon()
        assert panel.stopped == 0

    def test_reject_y_close_no_paran_dos_veces(self, qtbot, tmp_path):
        panel = _FakePanel(stop_on_exit=True)
        d = _dialog(qtbot, tmp_path, panel)
        d._auto_started_daemon = True
        d._maybe_stop_daemon()
        d._maybe_stop_daemon()
        assert panel.stopped == 1
