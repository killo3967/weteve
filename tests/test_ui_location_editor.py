"""Celda "URL o fichero": editor con botón para examinar ficheros locales.

Cuando la ubicación es un fichero local (no http), el editor debe ofrecer
un botón «…» que abre un QFileDialog y vuelca la ruta elegida en el campo.
"""

import pytest

from src.infrastructure.ui.source_catalog_widget import (
    LocationDelegate,
    SourceCatalogWidget,
)

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QFileDialog, QLineEdit, QPushButton  # noqa: E402


@pytest.fixture
def widget(qtbot):
    w = SourceCatalogWidget("m3u")
    qtbot.addWidget(w)
    w.add_row()
    return w


@pytest.fixture
def editor(qtbot, widget):
    index = widget.table.model().index(0, 2)
    delegate = widget.table.itemDelegateForColumn(2)
    ed = delegate.createEditor(widget.table, None, index)
    yield ed, index, delegate
    ed.deleteLater()


class TestLocationEditor:
    def test_columna_2_usa_delegado_propio(self, widget):
        delegate = widget.table.itemDelegateForColumn(2)
        assert isinstance(delegate, LocationDelegate)

    def test_editor_tiene_campo_y_boton_examinar(self, editor):
        ed, _index, _delegate = editor
        line = ed.findChild(QLineEdit)
        btn = ed.findChild(QPushButton)
        assert line is not None
        assert btn is not None
        assert "…" in btn.text()

    def test_editor_precarga_valor_de_la_celda(self, editor, widget):
        ed, index, delegate = editor
        widget.table.item(0, 2).setText("C:/datos/lista.m3u")
        delegate.setEditorData(ed, index)
        assert ed.findChild(QLineEdit).text() == "C:/datos/lista.m3u"

    def test_boton_abre_dialogo_y_vuelca_ruta(self, editor, monkeypatch):
        ed, _index, _delegate = editor
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: ("C:/descargas/nueva.m3u", "")))
        ed.findChild(QPushButton).click()
        # QDir.toNativeSeparators normaliza a barras de Windows
        assert ed.findChild(QLineEdit).text() == "C:\\descargas\\nueva.m3u"

    def test_boton_confirma_y_cierra_editor(self, editor, qtbot,
                                            monkeypatch):
        ed, index, delegate = editor
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: ("C:/descargas/nueva.m3u", "")))
        ed.findChild(QPushButton).click()
        delegate.setModelData(ed, index.model(), index)
        # QDir.toNativeSeparators normaliza a barras de Windows
        assert widget_value(index) == "C:\\descargas\\nueva.m3u"

    def test_boton_deshabilitado_si_es_url(self, editor):
        ed, _index, _delegate = editor
        line = ed.findChild(QLineEdit)
        btn = ed.findChild(QPushButton)
        line.setText("http://servidor/lista.m3u")
        # el estado del botón sigue al texto: emitir la señal de edición
        ed._signals.commit.emit()
        assert not btn.isEnabled()
        line.setText("C:/x.m3u")
        ed._signals.commit.emit()
        assert btn.isEnabled()


class TestFiltrosPorTipo:
    def test_filtro_epg_incluye_xml_gz_mlx(self, qtbot):
        w = SourceCatalogWidget("epg")
        qtbot.addWidget(w)
        delegate = w.table.itemDelegateForColumn(2)
        for ext in (".xml", ".xmltv", ".gz", ".mlx"):
            assert ext in delegate.FILTER, f"falta {ext} en filtro EPG"

    def test_filtro_m3u_incluye_m3u8(self, qtbot):
        w = SourceCatalogWidget("m3u")
        qtbot.addWidget(w)
        delegate = w.table.itemDelegateForColumn(2)
        for ext in (".m3u", ".m3u8"):
            assert ext in delegate.FILTER


def widget_value(index):
    return index.model().data(index)
