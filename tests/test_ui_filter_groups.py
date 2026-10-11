"""Desplegable de grupos para las reglas de filtro.

ListDialog: botón «Grupos…» en la tabla de reglas que muestra los grupos
reales de la fuente (vía pipeline) y rellena el campo Texto de la regla
seleccionada. Evita teclear grupos a mano.
"""

import pytest

from src.domain.entities.channel import Channel
from PyQt6.QtWidgets import QComboBox
from src.infrastructure.ui.list_manager_dialog import FilterRulesWidget


class _FakeDialog:
    """Mínimo que FilterRulesWidget necesita para pedir grupos."""

    def __init__(self, groups):
        self._groups = groups

    def available_groups(self):
        return list(self._groups)


@pytest.fixture()
def rules_widget(qtbot):
    w = FilterRulesWidget()
    qtbot.addWidget(w)
    w._add_row()  # una regla vacía
    return w


class TestAvailableGroups:
    def test_pipeline_get_groups_ordenados_sin_duplicados(self):
        from src.application.services.list_manager.pipeline import ListManagerPipeline
        chs = [
            Channel(name="a", url="u1", group="Deportes"),
            Channel(name="b", url="u2", group="Cine"),
            Channel(name="c", url="u3", group="Cine"),
            Channel(name="d", url="u4", group=""),  # vacío: excluido
        ]
        p = ListManagerPipeline(m3u_repository=_FakeM3URepoGroups(chs))
        p.register_source("s1", "fake.m3u", None)
        assert p.get_groups("s1") == ["Cine", "Deportes"]

    def test_pipeline_get_groups_fuente_desconocida_vacio(self):
        from src.application.services.list_manager.pipeline import ListManagerPipeline
        p = ListManagerPipeline(m3u_repository=_FakeM3URepoGroups([]))
        assert p.get_groups("noexiste") == []

    def test_widget_current_groups_via_dialogo(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["Cine", "Deportes"]))
        qtbot.addWidget(w)
        assert w._current_groups() == ["Cine", "Deportes"]


class _FakeM3URepoGroups:
    def __init__(self, channels):
        self._channels = channels

    def get_channels(self, source):
        return list(self._channels)


class TestPickGroup:
    def test_sin_dialogo_no_explota(self, rules_widget):
        assert rules_widget._current_groups() == []

    def test_rellena_texto_de_la_fila(self, qtbot, rules_widget):
        rules_widget._dialog = _FakeDialog(["Cine", "Deportes"])
        rules_widget._choose_group = lambda groups: "Cine"  # sin menú modal
        rules_widget.table.setCurrentCell(0, 1)
        rules_widget._pick_group_for_row(0)
        assert rules_widget.table.cellWidget(0, 1).currentText() == "Cine"
        # campo cambiado a "Grupo" automáticamente
        combo = rules_widget.table.cellWidget(0, 0)
        assert combo.currentData() == "group-title"

    def test_cancelar_no_cambia_nada(self, qtbot, rules_widget):
        rules_widget._dialog = _FakeDialog(["Cine"])
        rules_widget._choose_group = lambda groups: None  # usuario cancela
        rules_widget.table.item(0, 1).setText("preexistente")
        rules_widget._pick_group_for_row(0)
        assert rules_widget.table.item(0, 1).text() == "preexistente"

    def test_sin_grupos_muestra_aviso_y_no_cambia(self, qtbot, rules_widget, monkeypatch):
        rules_widget._dialog = _FakeDialog([])
        warnings = []
        rules_widget._warn_no_groups = lambda: warnings.append(1)
        rules_widget.table.item(0, 1).setText("preexistente")
        rules_widget._pick_group_for_row(0)
        assert warnings == [1]
        assert rules_widget.table.item(0, 1).text() == "preexistente"

    def test_aplicar_cambia_campo_a_grupo(self, qtbot, rules_widget):
        """Elegir un grupo con campo Nombre lo convierte a Grupo."""
        # la fila nueva es custom-filter por defecto
        combo = rules_widget.table.cellWidget(0, 0)
        assert combo.currentData() == "custom-filter"
        rules_widget._apply_group_to_row(0, "Cine")
        assert rules_widget.table.cellWidget(0, 1).currentText() == "Cine"
        assert rules_widget.table.cellWidget(0, 0).currentData() == "group-title"


class TestGroupCellCombo:
    """Cuando Campo = Grupo, el texto de la regla es un desplegable de grupos."""

    def test_fila_grupo_usa_combo_con_grupos(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA", "VOD ES"]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        cell = w.table.cellWidget(0, 1)
        assert isinstance(cell, QComboBox)
        assert [cell.itemText(i) for i in range(cell.count())] == \
            ["SPAIN", "USA", "VOD ES"]
        assert cell.currentText() == "SPAIN"

    def test_fila_nombre_usa_texto_libre(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN"]))
        qtbot.addWidget(w)
        w._add_row(field="custom-filter", text="La 1")
        assert isinstance(w.table.cellWidget(0, 1), QComboBox) is False
        assert w.table.item(0, 1).text() == "La 1"

    def test_get_rules_lee_combo_de_grupo(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN"]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        rules = w.get_rules()
        assert len(rules) == 1
        assert rules[0].field == "group-title"
        assert rules[0].pattern == "SPAIN"

    def test_cambio_de_campo_convierte_celda(self, qtbot):
        """Cambiar el combo Campo alterna entre combo de grupos y texto libre."""
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA"]))
        qtbot.addWidget(w)
        w._add_row(field="custom-filter", text="La 1")
        combo_campo = w.table.cellWidget(0, 0)
        combo_campo.setCurrentIndex(0)  # Grupo
        assert isinstance(w.table.cellWidget(0, 1), QComboBox)
        combo_campo.setCurrentIndex(1)  # Nombre
        assert not isinstance(w.table.cellWidget(0, 1), QComboBox)
        assert w.table.item(0, 1).text() == "La 1"  # conserva el texto

    def test_combo_grupo_es_editable_sin_lista(self, qtbot):
        """Sin grupos cargados aún, el combo queda vacío pero editable."""
        w = FilterRulesWidget(dialog=_FakeDialog([]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        cell = w.table.cellWidget(0, 1)
        assert isinstance(cell, QComboBox)
        assert cell.isEditable()
        assert cell.currentText() == "SPAIN"
