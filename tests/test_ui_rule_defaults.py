"""Valores por defecto al añadir una fila de regla nueva."""
import pytest

from src.infrastructure.ui.list_manager_dialog import FilterRulesWidget


@pytest.fixture()
def widget(qtbot):
    w = FilterRulesWidget()
    qtbot.addWidget(w)
    return w


class TestNuevaReglaPorDefecto:
    def test_boton_anadir_crea_and_excluir(self, widget, qtbot):
        from PyQt6.QtWidgets import QPushButton
        add = [b for b in widget.findChildren(QPushButton) if "Añadir" in b.text()][0]
        add.click()
        assert widget.table.cellWidget(0, 0).currentData() == "and"
        assert widget.table.cellWidget(0, 3).currentData() is True

    def test_add_row_sin_argumentos_usa_and_excluir(self, widget):
        widget._add_row(field="custom-filter", text="HBO")
        rule = widget.get_rules()[0]
        assert rule.operator == "and"
        assert rule.exclude is True
        assert rule.pattern == "!{HBO}"

    def test_valores_explicitos_mandan(self, widget):
        widget._add_row(field="group-title", text="SPAIN",
                        exclude=False, operator="or")
        rule = widget.get_rules()[0]
        assert rule.operator == "or"
        assert rule.exclude is False

    def test_grupos_preserva_inclusion_como_base(self, widget):
        """Con tabla vacía, "Grupos…" crea fila de INCLUSIÓN (OR), no de
        exclusión: elegir un grupo es añadir canales, no quitarlos."""
        widget._pick_group_for_row = lambda r: widget._set_text_cell(r, "group-title", "SPAIN")
        widget._pick_group()
        assert widget.table.cellWidget(0, 0).currentData() == "or"
        assert widget.table.cellWidget(0, 3).currentData() is False

    def test_load_rules_no_lesiona_operadores_guardados(self, widget):
        from src.application.services.list_manager.models import FilterRule
        widget.load_rules([
            FilterRule(field="group-title", pattern="SPAIN"),
            FilterRule(field="custom-filter", pattern="!{HBO}", exclude=True,
                       operator="and"),
        ])
        assert widget.table.cellWidget(0, 0).currentData() == "or"
        assert widget.table.cellWidget(0, 3).currentData() is False
        assert widget.table.cellWidget(1, 0).currentData() == "and"
        assert widget.table.cellWidget(1, 3).currentData() is True

    def test_move_row_conserva_valores(self, widget):
        widget._add_row(field="group-title", text="SPAIN", exclude=False,
                        operator="or")
        widget._add_row(field="custom-filter", text="HBO")
        widget._move_row(1, -1)
        assert widget.table.cellWidget(0, 0).currentData() == "and"
        assert widget.table.cellWidget(0, 3).currentData() is True
        assert widget.table.cellWidget(1, 0).currentData() == "or"
