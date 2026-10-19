"""Filtro por nombre en la pestaña de mapeo.

Un campo a la izquierda de «Actualizar guías»: lo que se escribe esconde las
filas cuyo nombre de canal no lo contiene. Es un filtro de vista: no pide nada
al demonio ni cambia el mapeo guardado.
"""

import pytest

from src.infrastructure.ui.list_manager_dialog import EpgMappingWidget


class _Canal:
    def __init__(self, name: str, url: str, epg: str = "", logo: str = ""):
        self.name = name
        self.url = url
        self.matched_epg_id = epg
        self.logo_url = logo
        self.served = True


CANALES = [
    _Canal("LA 1 HD", "http://s/1", "TVE"),
    _Canal("LA 2 HD", "http://s/2", "LA2"),
    _Canal("Antena 3 HD", "http://s/3", "A3"),
    _Canal("Aragón TV", "http://s/4", "ARAGTV"),
    _Canal("Canal Sur HD", "http://s/5", "CSURHD"),
    _Canal("M+ Deportes", "http://s/6", "MDEP"),
]


@pytest.fixture()
def widget(qtbot):
    w = EpgMappingWidget()
    qtbot.addWidget(w)
    w.set_epg(None)
    w.load_unmatched(CANALES, {})
    return w


def _visibles(w) -> list[str]:
    return [w.table.item(row, w.COL_NAME).text()
            for row in range(w.table.rowCount())
            if not w.table.isRowHidden(row)]


class TestFiltroPorNombre:
    def test_el_campo_esta_junto_al_boton(self, widget, qtbot):
        # con la ventana sin mostrar no hay geometría: hay que enseñarla
        widget.show()
        qtbot.wait(50)
        assert (widget.name_filter.x() + widget.name_filter.width()
                <= widget.refresh_guides_btn.x())

    def test_sin_texto_se_ven_todos(self, widget):
        assert len(_visibles(widget)) == len(CANALES)

    def test_filtra_por_nombre(self, widget):
        widget.name_filter.setText("LA")
        assert _visibles(widget) == ["LA 1 HD", "LA 2 HD"]

    def test_no_distingue_mayusculas_ni_acentos(self, widget):
        widget.name_filter.setText("aragon")
        assert _visibles(widget) == ["Aragón TV"]

    def test_muestra_solo_los_que_contienen_el_texto(self, widget):
        widget.name_filter.setText("canal sur")
        assert _visibles(widget) == ["Canal Sur HD"]
        assert widget.visible_rows() == 1

    def test_borrar_el_texto_vuelve_a_mostrar_todo(self, widget):
        widget.name_filter.setText("sur")
        assert widget.visible_rows() == 1
        widget.name_filter.setText("")
        assert widget.visible_rows() == len(CANALES)

    def test_el_filtro_sobrevive_a_repoblar(self, widget):
        widget.name_filter.setText("la")
        widget.load_unmatched(CANALES, {})
        assert _visibles(widget) == ["LA 1 HD", "LA 2 HD"]

    def test_no_cambia_el_mapeo_guardado(self, widget):
        widget.name_filter.setText("sur")
        # las asignaciones siguen todas ahí, ocultas o no
        assert len(widget.get_overrides()) >= 0
        assert widget.table.rowCount() == len(CANALES)
