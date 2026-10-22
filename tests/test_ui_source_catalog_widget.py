"""Tests del widget de cartera de fuentes (tabla multi-fila por tipo)."""

import pytest
from PyQt6.QtCore import Qt

from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    SourceEntry,
)
from src.infrastructure.ui.source_catalog_widget import SourceCatalogWidget

pytest.importorskip("PyQt6")


def _entry(eid="a", name="principal", kind="m3u", stype="url",
           loc="http://s/l.m3u", refresh=30, enabled=True):
    return SourceEntry(eid, name, kind, stype, loc, refresh, enabled)


class TestSourceCatalogWidget:
    def test_carga_filas_de_la_cartera(self, qtbot):
        w = SourceCatalogWidget(kind="m3u",
                                catalog=SourceCatalog([_entry()]))
        qtbot.addWidget(w)
        assert w.table.rowCount() == 1
        assert w.table.item(0, 0).text() == "principal"
        assert w.table.item(0, 1).text() == "URL remota"
        assert w.table.item(0, 2).text() == "http://s/l.m3u"
        assert w.table.item(0, 3).text() == "30"

    def test_filtra_por_tipo(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([
            _entry("a", "m3u one", "m3u"), _entry("b", "epg one", "epg"),
        ]))
        qtbot.addWidget(w)
        assert w.table.rowCount() == 1

    def test_anadir_fila(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([]))
        qtbot.addWidget(w)
        w.add_row()
        assert w.table.rowCount() == 1
        # valores por defecto razonables
        assert w.table.item(0, 1).text() == "Fichero local"
        assert w.table.item(0, 3).text() == "0"

    def test_eliminar_fila_seleccionada(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([
            _entry("a", "una"), _entry("b", "dos"),
        ]))
        qtbot.addWidget(w)
        w.table.selectRow(0)
        w.remove_selected()
        assert w.table.rowCount() == 1
        assert w.table.item(0, 0).text() == "dos"

    def test_get_catalog_redondea(self, qtbot):
        cat = SourceCatalog([_entry(refresh=30)])
        w = SourceCatalogWidget(kind="m3u", catalog=cat)
        qtbot.addWidget(w)
        w.table.item(0, 0).setText("renombrada")
        w.table.item(0, 3).setText("15")
        out = w.get_catalog()
        assert out.get("a").name == "renombrada"
        assert out.get("a").refresh_min == 15
        # el id y el tipo no cambian al editar
        assert out.get("a").id == "a"
        assert out.get("a").source_type == "url"

    def test_activa_como_checkbox(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([
            _entry("a", enabled=True), _entry("b", enabled=False),
        ]))
        qtbot.addWidget(w)
        # Activa es la última columna: antes va la prioridad de la fuente
        assert w.table.item(0, 5).checkState() == Qt.CheckState.Checked
        assert w.table.item(1, 5).checkState() == Qt.CheckState.Unchecked

    def test_get_seleccion_devuelve_ids_activos(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([
            _entry("a"), _entry("b", enabled=False),
        ]))
        qtbot.addWidget(w)
        assert w.selected_ids() == ["a"]

    def test_set_seleccion_marca_filas(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([
            _entry("a"), _entry("b"),
        ]))
        qtbot.addWidget(w)
        w.set_selection(["b"])
        assert w.selected_ids() == ["b"]

    def test_fila_nueva_obtiene_id_nuevo(self, qtbot):
        w = SourceCatalogWidget(kind="m3u", catalog=SourceCatalog([]))
        qtbot.addWidget(w)
        w.add_row()
        w.table.item(0, 0).setText("nueva")
        w.table.item(0, 2).setText("C:/x.m3u")
        out = w.get_catalog()
        assert len(out.all()) == 1
        assert out.all()[0].id  # id generado, no vacío


class TestPrioridadDeLaFuente:
    """El peso decide qué guía manda cuando dos anuncian el mismo canal."""

    def test_la_prioridad_se_edita_y_se_lee(self, qtbot):
        w = SourceCatalogWidget(kind="epg", catalog=SourceCatalog([
            _entry("a", kind="epg"),
        ]))
        qtbot.addWidget(w)
        assert w.table.item(0, 4).text() == "0"
        w.table.item(0, 4).setText("10")
        assert w.get_catalog().get("a").priority == 10

    def test_prioridad_no_numerica_vale_cero(self, qtbot):
        w = SourceCatalogWidget(kind="epg", catalog=SourceCatalog([
            _entry("a", kind="epg"),
        ]))
        qtbot.addWidget(w)
        w.table.item(0, 4).setText("alta")
        assert w.get_catalog().get("a").priority == 0
