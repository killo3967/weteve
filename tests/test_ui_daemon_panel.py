"""Tests del panel Qt «Demonio» (DaemonPanel).

Usa un cliente falso (no hay sockets): valida estados, refresco y aplicar.
"""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QLabel  # noqa: E402

from src.infrastructure.ui.daemon_panel import DaemonPanel  # noqa: E402


class _FakeClient:
    """DaemonClient falso: respuestas programables."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.reload_calls: list[str] = []
        self.shutdowns: list[int] = []
        self.status_payload = {"targets": {"0": {"name": "Mi lista",
                                                 "running": True,
                                                 "channel_count": 42}}}

    def ping(self) -> bool:
        return self.alive

    def status(self) -> dict | None:
        return self.status_payload if self.alive else None

    def shutdown(self) -> bool:
        self.shutdowns.append(1)
        return self.alive

    def reload_target(self, target_id: str) -> bool:
        self.reload_calls.append(target_id)
        return self.alive


class _FakeStore:
    def __init__(self) -> None:
        self.saved: dict = {}

    def save(self, target_id: str, target, epg_path=None, bind=None) -> None:
        self.saved[target_id] = target


class _FakeLauncher:
    def __init__(self) -> None:
        self.launched = 0

    def launch(self) -> bool:
        self.launched += 1
        return True


@pytest.fixture()
def panel(qtbot):
    client = _FakeClient()
    store = _FakeStore()
    p = DaemonPanel(client=client, target_store=store, target_id="0",
                    get_target_config=lambda: {"name": "Mi lista"})
    qtbot.addWidget(p)
    return p, client, store


class TestLifecycleButtons:
    def test_start_arranca_y_refresca(self, panel) -> None:
        p, client, _ = panel
        client.alive = False
        launcher = _FakeLauncher()
        p._launcher = launcher
        p.start_daemon()
        assert launcher.launched == 1
        assert "arrancando" in p.status_label.text().lower()

    def test_start_si_ya_vivo_no_lanza(self, panel) -> None:
        p, client, _ = panel
        launcher = _FakeLauncher()
        p._launcher = launcher
        p.start_daemon()  # daemon ya vivo
        assert launcher.launched == 0

    def test_stop_pide_shutdown(self, panel) -> None:
        p, client, _ = panel
        p._client.shutdowns = []
        original = client.shutdown
        client.shutdown = lambda: (client.shutdowns.append(1), True)[1]
        p.stop_daemon()
        assert client.shutdowns == [1]
        assert "parando" in p.status_label.text().lower()

    def test_restart_es_stop_mas_start(self, panel) -> None:
        p, client, _ = panel
        calls = []

        def _stop() -> bool:
            calls.append("stop")
            client.alive = False  # el daemon ya no responde tras el shutdown
            return True

        client.shutdown = _stop
        launcher = _FakeLauncher()
        p._launcher = launcher
        p.restart_daemon()
        assert calls == ["stop"]
        assert launcher.launched == 1

    def test_restart_daemon_parado_solo_arranca(self, panel) -> None:
        p, client, _ = panel
        client.alive = False
        calls = []
        client.shutdown = lambda: (calls.append("stop"), False)[1]
        launcher = _FakeLauncher()
        p._launcher = launcher
        p.restart_daemon()
        assert calls == ["stop"]
        assert launcher.launched == 1

    def test_restart_aun_apagandose_no_relanza(self, panel) -> None:
        """Si tras el shutdown el daemon sigue respondiendo, no lanzar otro."""
        p, client, _ = panel
        launcher = _FakeLauncher()
        p._launcher = launcher
        p.restart_daemon()  # alive=True: shutdown OK y sigue respondiendo
        assert launcher.launched == 0
        assert "reiniciando" in p.status_label.text().lower()


class TestDaemonPanel:
    def test_daemon_activo_muestra_estado(self, panel) -> None:
        p, _client, _store = panel
        p.refresh_status()
        assert "activo" in p.status_label.text().lower()

    def test_daemon_parado(self, qtbot) -> None:
        client = _FakeClient(alive=False)
        p = DaemonPanel(client=client, target_store=_FakeStore(),
                        target_id="0", get_target_config=lambda: {})
        qtbot.addWidget(p)
        p.refresh_status()
        assert "no responde" in p.status_label.text().lower()

    def test_apply_guarda_y_recarga(self, panel) -> None:
        p, client, store = panel
        p.apply_to_daemon()
        assert "0" in store.saved
        assert client.reload_calls == ["0"]

    def test_apply_sin_daemon_no_recarga(self, qtbot) -> None:
        client = _FakeClient(alive=False)
        store = _FakeStore()
        p = DaemonPanel(client=client, target_store=store, target_id="0",
                        get_target_config=lambda: {})
        qtbot.addWidget(p)
        p.apply_to_daemon()
        assert "0" in store.saved  # se guarda igualmente
        assert "no responde" in p.status_label.text().lower()

    def test_apply_sin_store_solo_recarga(self, panel) -> None:
        p, client, _store = panel
        p._store = None
        p.apply_to_daemon()
        assert client.reload_calls == ["0"]
