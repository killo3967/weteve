"""Log tail: un EOF antes de tiempo no puede colgar la GUI.

El bucle de lectura avanza por trozos; si el fichero encoge o el tamaño
anunciado no coincide con lo legible, `read` devuelve b"" y sin la salida
explícita el `while` no termina (la ventana se quedaba congelada).
"""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")


@pytest.fixture
def widget(qtbot, tmp_path: Path):
    from src.infrastructure.ui.log_tail_widget import LogTailWidget

    log = tmp_path / "weteve.log"
    log.write_text("linea 1\nlinea 2\n", encoding="utf-8")
    w = LogTailWidget(log)
    qtbot.addWidget(w)
    w._timer.stop()
    w._initial_timer.stop()
    return w


class TestLogTail:
    def test_lee_lo_nuevo(self, widget):
        widget._poll()
        assert "linea 2" in widget.view.toPlainText()

    def test_tamano_anunciado_mayor_que_el_contenido_no_cuelga(self, widget,
                                                              monkeypatch):
        """stat dice más bytes de los legibles: antes era un bucle infinito."""
        real_stat = Path.stat

        def stat_inflado(self, *args, **kwargs):
            st = real_stat(self, *args, **kwargs)
            return type(st)(tuple(st[:6]) + (st.st_size + 1_000_000,)
                            + tuple(st[7:]))

        monkeypatch.setattr(Path, "stat", stat_inflado)
        widget._poll()          # sin la salida por EOF, esto no terminaría
        assert widget._offset <= 1_000_000

    def test_fichero_que_no_existe_no_rompe(self, qtbot, tmp_path):
        from src.infrastructure.ui.log_tail_widget import LogTailWidget

        w = LogTailWidget(tmp_path / "no.log")
        qtbot.addWidget(w)
        w._poll()
        assert w.view.toPlainText() == ""
