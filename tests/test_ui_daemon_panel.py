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
        self.status_payload = {"targets": {"0": {"name": "Mi lista",
                                                 "running": True,
                                                 "channel_count": 42}}}

    def ping(self) -> bool:
        return self.alive

    def status(self) -> dict | None:
        return self.status_payload if self.alive else None

    def reload_target(self, target_id: str) -> bool:
        self.reload_calls.append(target_id)
        return self.alive


class _FakeStore:
    def __init__(self) -> None:
        self.saved: dict = {}

    def save(self, target_id: str, target, epg_path=None, bind=None) -> None:
        self.saved[target_id] = target


@pytest.fixture()
def panel(qtbot):
    client = _FakeClient()
    store = _FakeStore()
    p = DaemonPanel(client=client, target_store=store, target_id="0",
                    get_target_config=lambda: {"name": "Mi lista"})
    qtbot.addWidget(p)
    return p, client, store


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
