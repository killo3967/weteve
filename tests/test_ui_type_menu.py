"""Celda "Tipo de origen": menú emergente al hacer clic (sin editor in-cell).

El editor-in-cell de combo se renderiza mal con el tema oscuro del
sistema (flecha invisible, textos superpuestos en el popup). La celda
ya no es editable en el sentido Qt: un clic muestra un QMenu nativo con
las dos opciones y aplica la elección directamente.
"""

import pytest

from src.infrastructure.ui.source_catalog_widget import SourceCatalogWidget

pytest.importorskip("PyQt6")



@pytest.fixture
def widget(qtbot):
    w = SourceCatalogWidget("m3u")
    qtbot.addWidget(w)
    w.add_row()
    # cerrar el editor autoabierto de la ubicación
    from PyQt6.QtWidgets import QStyledItemDelegate
    d = w.table.itemDelegateForColumn(2)
    d.commitData.emit(w.table.focusWidget())
    d.closeEditor.emit(w.table.focusWidget(),
                       QStyledItemDelegate.EndEditHint.NoHint)
    return w


class TestMenuTipoOrigen:
    def test_la_celda_tipo_no_usa_delegado_de_editor(self, widget):
        from PyQt6.QtWidgets import QStyledItemDelegate
        delegate = widget.table.itemDelegateForColumn(1)
        assert delegate is None or type(delegate) is QStyledItemDelegate

    def test_clic_en_tipo_muestra_menu_con_las_dos_opciones(self, widget,
                                                            qtbot):
        actions = []
        widget._show_type_menu = lambda row, menu=None: actions.extend(
            ["Fichero local", "URL remota"])
        widget.table.cellClicked.emit(0, 1)
        assert actions == ["Fichero local", "URL remota"]

    def test_menu_contiene_las_dos_opciones(self, widget):
        menu = widget._build_type_menu(0)
        texts = [a.text() for a in menu.actions()]
        assert texts == ["Fichero local", "URL remota"]

    def test_elegir_url_prefija_http_y_actualiza_tipo(self, widget):
        menu = widget._build_type_menu(0)
        # disparar la acción "URL remota" (triggers de QAction no abren nada)
        menu.actions()[1].trigger()
        assert widget.table.item(0, 1).text() == "URL remota"
        assert widget.table.item(0, 2).text() == "http://"

    def test_elegir_fichero_no_toca_ubicacion_http(self, widget):
        widget.table.item(0, 2).setText("http://s/l.m3u")
        widget.table.item(0, 1).setText("URL remota")
        widget._apply_type_choice(0, "Fichero local")
        assert widget.table.item(0, 2).text() == "http://s/l.m3u"
        assert widget.table.item(0, 1).text() == "URL remota"

    def test_elegir_fichero_con_ruta_local_actualiza_tipo(self, widget):
        widget.table.item(0, 2).setText("C:/x/l.m3u")
        widget.table.item(0, 1).setText("URL remota")
        widget._apply_type_choice(0, "Fichero local")
        assert widget.table.item(0, 1).text() == "Fichero local"
