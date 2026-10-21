"""Candado de instancia única del demonio.

Un solo demonio puede estar vivo aunque haya dos ``control_port`` distintos:
el candado va junto a ``daemon.toml`` (el recurso que se pisa) y se toma antes
de leer la configuración. El segundo proceso avisa de dónde está el que corre.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import daemon as daemon_mod
from src.infrastructure.client import daemon_launcher
from src.infrastructure.client.single_instance import (
    DaemonLock,
    held,
    lock_path,
    pid_path,
    read_address,
    write_pid,
)


class TestCandado:
    def test_el_primero_toma_y_el_segundo_no(self, tmp_path: Path):
        ruta = lock_path(tmp_path / "daemon.toml")
        primero = DaemonLock(ruta)
        assert primero.acquire()
        try:
            assert not DaemonLock(ruta).acquire()
            assert held(ruta)
        finally:
            primero.release()
        assert not held(ruta)
        segundo = DaemonLock(ruta)
        assert segundo.acquire()
        segundo.release()

    def test_la_direccion_se_lee_con_el_candado_tomado(self, tmp_path: Path):
        ruta = lock_path(tmp_path / "daemon.toml")
        candado = DaemonLock(ruta)
        assert candado.acquire()
        try:
            write_pid(pid_path(tmp_path / "daemon.toml"), "127.0.0.1:34400")
            assert read_address(pid_path(tmp_path / "daemon.toml")) == (
                "127.0.0.1:34400")
        finally:
            candado.release()

    def test_otro_proceso_lee_la_direccion_y_ve_el_candado(
            self, tmp_path: Path):
        ruta = lock_path(tmp_path / "daemon.toml")
        candado = DaemonLock(ruta)
        assert candado.acquire()
        write_pid(pid_path(tmp_path / "daemon.toml"), "127.0.0.1:9999")
        try:
            raiz = Path(__file__).resolve().parents[1]
            pid = pid_path(tmp_path / "daemon.toml")
            codigo = (
                f"import sys; sys.path.insert(0, r'{raiz}');"
                "from src.infrastructure.client.single_instance import "
                "read_address, held;"
                f"print(read_address(r'{pid}'), held(r'{ruta}'))")
            salida = subprocess.run([sys.executable, "-c", codigo],
                                    capture_output=True, text=True, timeout=60)
            assert salida.stdout.split() == ["127.0.0.1:9999", "True"]
        finally:
            candado.release()

    def test_sin_fichero_no_hay_direccion_ni_candado(self, tmp_path: Path):
        ruta = lock_path(tmp_path / "daemon.toml")
        assert not held(ruta)
        assert read_address(ruta) is None


class TestArranque:
    """:func:`daemon.cmd_run` sale avisando en vez de abrir un segundo."""

    def _preparar(self, monkeypatch, tmp_path: Path, vivo: bool):
        monkeypatch.setattr(daemon_mod, "setup_logging", lambda: None)
        monkeypatch.setattr(daemon_mod, "_hay_demonio", lambda *a, **k: vivo)
        ruta = lock_path(tmp_path / "daemon.toml")

        def no_construir(*_a, **_k):
            raise AssertionError("no se debe construir un segundo demonio")

        monkeypatch.setattr(daemon_mod, "build_daemon", no_construir)
        candado = DaemonLock(ruta)
        assert candado.acquire()
        write_pid(pid_path(tmp_path / "daemon.toml"), "127.0.0.1:34400")
        return candado, ruta

    def test_avisa_y_sale_cero_si_ya_esta_en_marcha(
            self, monkeypatch, tmp_path: Path, capsys):
        candado, _ruta = self._preparar(monkeypatch, tmp_path, vivo=True)
        try:
            assert daemon_mod.cmd_run(tmp_path / "daemon.toml") == 0
        finally:
            candado.release()
        salida = capsys.readouterr().out
        assert "ya está en marcha en 127.0.0.1:34400" in salida

    def test_sale_con_error_si_el_candado_no_responde(
            self, monkeypatch, tmp_path: Path, capsys):
        candado, _ruta = self._preparar(monkeypatch, tmp_path, vivo=False)
        try:
            assert daemon_mod.cmd_run(tmp_path / "daemon.toml") == 2
        finally:
            candado.release()
        assert "no se arranca otro" in capsys.readouterr().out


class TestGui:
    def test_la_gui_encuentra_el_demonio_aunque_este_en_otro_puerto(
            self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(daemon_launcher, "_daemon_script",
                            lambda: tmp_path / "daemon.py")
        ruta = lock_path(tmp_path / "daemon.toml")
        candado = DaemonLock(ruta)
        assert candado.acquire()
        try:
            write_pid(pid_path(tmp_path / "daemon.toml"),
                      "127.0.0.1:39999")
            assert daemon_launcher.DaemonLauncher.daemon_address() == (
                "127.0.0.1:39999")
        finally:
            candado.release()
        assert daemon_launcher.DaemonLauncher.daemon_address() is None


@pytest.mark.parametrize("nombre", ["weteve.pid"])
def test_el_fichero_no_se_rastrea(nombre: str):
    ignora = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8")
    assert nombre in ignora
