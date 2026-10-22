"""Host de control: desplegable, validación del token y arranque verificado.

Tres fallos que costaron una tarde: una IP que no es del equipo (el demonio no
enlaza), un host de red sin token (no arranca) y un arranque que muere sin que
la interfaz se entere.
"""

import pytest

from src.infrastructure.client.daemon_launcher import DaemonLauncher
from src.infrastructure.client.net_utils import (
    announced_host,
    connect_host,
    host_options,
)


class TestDirecciones:
    def test_para_conectar_0000_es_loopback(self):
        assert connect_host("0.0.0.0") == "127.0.0.1"
        assert connect_host("localhost") == "127.0.0.1"
        assert connect_host("192.0.2.10") == "192.0.2.10"

    def test_para_anunciar_0000_no_sirve(self):
        assert announced_host("0.0.0.0") != "0.0.0.0"
        assert announced_host("192.0.2.10") == "192.0.2.10"
        assert announced_host("127.0.0.1") == "127.0.0.1"

    def test_el_desplegable_empieza_en_loopback_y_acaba_en_todos(self):
        opciones = host_options()
        valores = [valor for valor, _ in opciones]
        assert valores[0] == "127.0.0.1"
        assert valores[-1] == "0.0.0.0"
        assert len(valores) == len(set(valores))
        # la IP de la red va antes que las secundarias
        assert all(etiqueta for _, etiqueta in opciones)

    def test_un_valor_guardado_que_ya_no_existe_se_conserva(self):
        opciones = host_options("192.168.1.99")
        assert opciones[0][0] == "192.168.1.99"
        assert "no disponible" in opciones[0][1]

    def test_localhost_se_normaliza(self):
        assert host_options("localhost")[0][0] == "127.0.0.1"
        assert "localhost" not in [valor for valor, _ in host_options("localhost")]


class _Cliente:
    def __init__(self, vivo: bool) -> None:
        self.vivo = vivo

    def ping(self) -> bool:
        return self.vivo


class TestArranqueVerificado:
    def _launcher(self, monkeypatch, tmp_path, log: str = ""):
        monkeypatch.setattr(
            "src.infrastructure.client.daemon_launcher._daemon_script",
            lambda: tmp_path / "daemon.py")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "weteve-daemon.log").write_text(log,
                                                             encoding="utf-8")
        return DaemonLauncher()

    def test_arranca_y_responde(self, monkeypatch, tmp_path):
        launcher = self._launcher(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, "launch", lambda **_: True)
        assert launcher.start_and_wait(_Cliente(True), timeout=2) == (True, "")

    def test_muere_y_devuelve_el_motivo_del_log(self, monkeypatch, tmp_path):
        launcher = self._launcher(
            monkeypatch, tmp_path,
            log="2026-10-21 11:13:25,973 [error] weteve.daemon: mcp_token "
                "obligatorio cuando control_host no es loopback\n")
        monkeypatch.setattr(launcher, "launch", lambda **_: True)
        ok, motivo = launcher.start_and_wait(_Cliente(False), timeout=0.3)
        assert ok is False
        assert "mcp_token obligatorio" in motivo

    def test_si_no_se_puede_lanzar_lo_dice(self, monkeypatch, tmp_path):
        launcher = self._launcher(monkeypatch, tmp_path)
        monkeypatch.setattr(launcher, "launch", lambda **_: False)
        ok, motivo = launcher.start_and_wait(_Cliente(False), timeout=0.1)
        assert ok is False and "lanzar" in motivo


class TestGeneracionDelToken:
    def test_empieza_por_wtv(self):
        from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

        class _Campo:
            def __init__(self) -> None:
                self.texto = ""

            def setText(self, valor: str) -> None:  # noqa: N802 - API Qt
                self.texto = valor

        app = ListManagerDialog.__new__(ListManagerDialog)
        app.mcp_token_edit = _Campo()
        app._generar_token()
        assert app.mcp_token_edit.texto.startswith("wtv-")
        assert len(app.mcp_token_edit.texto) > len("wtv-")


class TestValidacionDelToken:
    def test_fuera_de_loopback_sin_token_no_se_guarda(self, qtbot):
        pytest.importorskip("PyQt6")
        from types import SimpleNamespace

        from src.infrastructure.ui.list_manager_dialog import ListManagerDialog

        app = ListManagerDialog.__new__(ListManagerDialog)
        app.parent = lambda: None
        sin_token = SimpleNamespace(control_host="192.0.2.10", mcp_token="")
        con_token = SimpleNamespace(control_host="192.0.2.10",
                                    mcp_token="abc")
        local = SimpleNamespace(control_host="127.0.0.1", mcp_token="")

        avisos: list = []
        monkeypatch = pytest.MonkeyPatch()
        try:
            import src.infrastructure.ui.list_manager_dialog as modulo
            monkeypatch.setattr(modulo.QMessageBox, "warning",
                                lambda *a, **k: avisos.append(a))
            assert app._host_valido(sin_token) is False
            assert avisos, "debe avisar de que falta el token"
            assert app._host_valido(con_token) is True
            assert app._host_valido(local) is True
        finally:
            monkeypatch.undo()
