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


class TestTipoOrigenCombo:
    def test_celda_tipo_es_editable_en_la_vista(self, widget):
        from PyQt6.QtCore import Qt
        flags = widget.table.item(0, 1).flags()
        assert flags & Qt.ItemFlag.ItemIsEditable

    def test_cambiar_combo_emite_commit_del_delegado(self, widget, qtbot):
        from PyQt6.QtWidgets import QComboBox
        delegate = widget.table.itemDelegateForColumn(1)
        idx = widget.table.model().index(0, 1)
        ed = delegate.createEditor(widget.table, None, idx)
        spy = []
        delegate.commitData.connect(
            lambda e: spy.append(e))
        ed.findChild(QComboBox).setCurrentText("URL remota")
        assert spy == [ed]

    def test_editor_de_tipo_es_combo(self, widget):
        delegate = widget.table.itemDelegateForColumn(1)
        from PyQt6.QtWidgets import QComboBox
        idx = widget.table.model().index(0, 1)
        ed = delegate.createEditor(widget.table, None, idx)
        combo = ed.findChild(QComboBox)
        assert combo is not None
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "Fichero local", "URL remota"]

    def test_elegir_url_con_ubicacion_vacia_prefija_http(self, widget):
        delegate = widget.table.itemDelegateForColumn(1)
        from PyQt6.QtWidgets import QComboBox
        idx = widget.table.model().index(0, 1)
        ed = delegate.createEditor(widget.table, None, idx)
        delegate.setEditorData(ed, idx)
        ed.findChild(QComboBox).setCurrentText("URL remota")
        delegate.setModelData(ed, widget.table.model(), idx)
        # ubicación preparada para completar la URL y tipo sincronizado
        assert widget.table.item(0, 2).text() == "http://"
        assert widget.table.item(0, 1).text() == "URL remota"

    def test_elegir_fichero_no_toca_ubicacion_http(self, widget):
        widget.table.item(0, 2).setText("http://s/l.m3u")
        delegate = widget.table.itemDelegateForColumn(1)
        from PyQt6.QtWidgets import QComboBox
        idx = widget.table.model().index(0, 1)
        ed = delegate.createEditor(widget.table, None, idx)
        ed.findChild(QComboBox).setCurrentText("Fichero local")
        delegate.setModelData(ed, widget.table.model(), idx)
        # una URL http no puede pasar a fichero: ubicación intacta,
        # y el tipo mostrado sigue reflejando la inferencia
        assert widget.table.item(0, 2).text() == "http://s/l.m3u"
        assert widget.table.item(0, 1).text() == "URL remota"

    def test_editar_ubicacion_actualiza_tipo_en_vivo(self, widget):
        widget.table.item(0, 2).setText("http://s/l.m3u")
        assert widget.table.item(0, 1).text() == "URL remota"
        widget.table.item(0, 2).setText("C:/x/l.m3u")
        assert widget.table.item(0, 1).text() == "Fichero local"


def widget_value(index):
    return index.model().data(index)
