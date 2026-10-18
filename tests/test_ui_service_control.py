"""El servicio de playlist vive en el daemon; la GUI solo manda órdenes.

Antes el diálogo tenía su propio PlaylistServer en el mismo puerto que el del
daemon: dos servidores peleando por 9999 y dos pipelines. Ahora
``_toggle_service`` persiste puerto/acceso, llama a ``/service`` y refleja el
estado que devuelve el daemon.
"""


import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QMessageBox

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


class _FakeApi:
    """Doble del ApiClient: registra órdenes y simula estado."""

    root_url = "http://127.0.0.1:34400"

    def __init__(self, running: bool = False, down: bool = False) -> None:
        self.running = running
        self.down = down
        self.calls: list[tuple] = []

    def target_status(self, target_id: str):
        if self.down:
            return 0, {"error": "sin conexión"}
        return 200, {
            "name": "test", "running": self.running, "port": 34400,
            "playlist_url": ("http://127.0.0.1:34400/weteve/0/playlist.m3u"
                             if self.running else None),
            "epg_url": ("http://127.0.0.1:34400/weteve/0/epg.xml"
                        if self.running else None),
        }

    def set_settings(self, target_id: str, **changes):
        self.calls.append(("settings", target_id, changes))
        return 200, {"ok": True}

    def service(self, target_id: str, action: str):
        self.calls.append(("service", target_id, action))
        self.running = action == "start"
        _, status = self.target_status(target_id)
        return 200, {"ok": True, "status": status}


@pytest.fixture
def dialog(qtbot, tmp_path, monkeypatch):
    from src.infrastructure.adapters import file_m3u_repository as repo

    monkeypatch.setattr(repo.FileM3URepository, "_get_content",
                        lambda self, source: ["#EXTM3U"])
    path = tmp_path / "config.ini"
    path.write_text("[listmanager.target.0]\nname = test\nport = 9999\n"
                    "bind = 127.0.0.1\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": path, "name": "test", "port": 9999,
        "bind": "127.0.0.1"})
    qtbot.addWidget(d)
    d._config_watch.stop()
    return d


@pytest.fixture(autouse=True)
def no_modal(monkeypatch):
    """Sin diálogos modales en los tests."""
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ""))
    return warnings


class TestServicioEnElDaemon:
    def test_el_dialogo_ya_no_tiene_servidor_propio(self, dialog):
        assert not hasattr(dialog, "_server")

    def test_iniciar_ordena_start_al_daemon(self, dialog):
        """El puerto es del demonio: aquí no se reconfigura nada."""
        api = _FakeApi(running=False)
        dialog._api_client = lambda: api
        dialog._toggle_service()
        assert [c[0] for c in api.calls] == ["service"]
        assert api.calls[0] == ("service", "0", "start")
        assert dialog.service_button.text() == "&Parar servicio"

    def test_parar_cuando_esta_sirviendo_no_reconfigura(self, dialog):
        api = _FakeApi(running=True)
        dialog._api_client = lambda: api
        dialog._toggle_service()
        assert [c[0] for c in api.calls] == ["service"]
        assert api.calls[-1] == ("service", "0", "stop")
        assert dialog.service_button.text() == "&Iniciar servicio"

    def test_daemon_caido_avisa_y_no_ordena_nada(self, dialog, no_modal):
        api = _FakeApi(down=True)
        dialog._api_client = lambda: api
        dialog._toggle_service()
        assert api.calls == []
        assert any("daemon" in w.lower() for w in no_modal)

    def test_muestra_la_url_servida_por_el_daemon(self, dialog):
        api = _FakeApi(running=True)
        dialog._api_client = lambda: api
        dialog._sync_service_url()
        assert "/weteve/0/playlist.m3u" in dialog.service_url_label.text()
