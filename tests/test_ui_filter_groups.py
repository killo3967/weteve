"""Desplegable de grupos para las reglas de filtro.

ListDialog: botón «Grupos…» en la tabla de reglas que muestra los grupos
reales de la fuente (vía pipeline) y rellena el campo Texto de la regla
seleccionada. Evita teclear grupos a mano.
"""

import pytest

from src.domain.entities.channel import Channel
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
        assert rules_widget.table.item(0, 1).text() == "Cine"
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
        """Elegir un grupo con campo Nombre/atributos lo convierte a Grupo."""
        # la fila nueva es custom-filter por defecto
        combo = rules_widget.table.cellWidget(0, 0)
        assert combo.currentData() == "custom-filter"
        rules_widget._apply_group_to_row(0, "Cine")
        assert rules_widget.table.item(0, 1).text() == "Cine"
        assert rules_widget.table.cellWidget(0, 0).currentData() == "group-title"
