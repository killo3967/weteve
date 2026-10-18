"""Fixtures comunes de los tests.

``fake_api`` (autouse) sustituye el ``ApiClient`` del diálogo por un doble: la
GUI es un frontend y, sin esto, cada consulta esperaría al daemon real (que en
los tests no está y, en esta máquina, un rechazo de conexión tarda ~2 s).

Los tests que necesitan datos concretos mutan los atributos del fake o
monkeypatchean ``_api_client`` en su instancia.
"""

import pytest
from fakes_api import FakeApi


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    api = FakeApi()
    from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

    monkeypatch.setattr(ListManagerDialog, "_api_client", lambda self: api)
    return api


@pytest.fixture(autouse=True)
def no_daemon_network(request, monkeypatch):
    """Los tests de UI no tocan la red del panel del demonio.

    Un rechazo de conexión a loopback tarda ~2 s en esta máquina; el panel
    llama a ``/status`` al abrir cada diálogo. Los tests del propio cliente
    (``test_daemon_client``) quedan fuera del parche.
    """
    if "test_ui_" not in request.node.nodeid:
        return
    from src.infrastructure.client.daemon_client import DaemonClient

    monkeypatch.setattr(DaemonClient, "_request",
                        lambda self, method, path, body=None: (0, None))


@pytest.fixture()
def wait_save():
    """Espera a que termine el guardado en segundo plano del diálogo.

    El lote HTTP va en un hilo de trabajo (el pipeline tarda segundos y no
    puede congelar la GUI): tras pulsar Guardar hay que dejar correr el bucle
    de eventos hasta que llegue el resultado.
    """
    def _wait(dialog, timeout: float = 10.0) -> None:
        import time

        from PyQt6.QtWidgets import QApplication

        fin = time.monotonic() + timeout
        while dialog.save_in_progress() and time.monotonic() < fin:
            QApplication.processEvents()
            time.sleep(0.005)
        assert not dialog.save_in_progress(), "el guardado no terminó"
    return _wait


# -- e2e ---------------------------------------------------------------------

@pytest.fixture()
def daemon(tmp_path):
    """Daemon real (``build_daemon``) con el target sirviendo."""
    from e2e_support import start

    handle = start(tmp_path)
    yield handle
    handle.stop()


@pytest.fixture()
def api(daemon):
    """``ApiClient`` real apuntando al daemon de la fixture."""
    return daemon.api()
