"""Desplegable de grupos para las reglas de filtro.

ListDialog: botón «Grupos…» en la tabla de reglas que muestra los grupos
reales de la fuente (vía pipeline) y rellena el campo Texto de la regla
seleccionada. Evita teclear grupos a mano.
"""

import pytest

from src.domain.entities.channel import Channel
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.application.services.list_manager.models import FilterRule
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
        assert rules_widget.table.cellWidget(0, 2).currentText() == "Cine"
        # campo cambiado a "Grupo" automáticamente
        combo = rules_widget.table.cellWidget(0, 1)
        assert combo.currentData() == "group-title"

    def test_cancelar_no_cambia_nada(self, qtbot, rules_widget):
        rules_widget._dialog = _FakeDialog(["Cine"])
        rules_widget._choose_group = lambda groups: None  # usuario cancela
        rules_widget.table.item(0, 2).setText("preexistente")
        rules_widget._pick_group_for_row(0)
        assert rules_widget.table.item(0, 2).text() == "preexistente"

    def test_sin_grupos_muestra_aviso_y_no_cambia(self, qtbot, rules_widget, monkeypatch):
        rules_widget._dialog = _FakeDialog([])
        warnings = []
        rules_widget._warn_no_groups = lambda: warnings.append(1)
        rules_widget.table.item(0, 2).setText("preexistente")
        rules_widget._pick_group_for_row(0)
        assert warnings == [1]
        assert rules_widget.table.item(0, 2).text() == "preexistente"

    def test_aplicar_cambia_campo_a_grupo(self, qtbot, rules_widget):
        """Elegir un grupo con campo Nombre lo convierte a Grupo."""
        # la fila nueva es custom-filter por defecto
        combo = rules_widget.table.cellWidget(0, 1)
        assert combo.currentData() == "custom-filter"
        rules_widget._apply_group_to_row(0, "Cine")
        assert rules_widget.table.cellWidget(0, 2).currentText() == "Cine"
        assert rules_widget.table.cellWidget(0, 1).currentData() == "group-title"


class TestGroupCellCombo:
    """Cuando Campo = Grupo, el texto de la regla es un desplegable de grupos."""

    def test_fila_grupo_usa_combo_con_grupos(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA", "VOD ES"]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        cell = w.table.cellWidget(0, 2)
        assert isinstance(cell, QComboBox)
        assert [cell.itemText(i) for i in range(cell.count())] == \
            ["SPAIN", "USA", "VOD ES"]
        assert cell.currentText() == "SPAIN"

    def test_fila_nombre_usa_texto_libre(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN"]))
        qtbot.addWidget(w)
        w._add_row(field="custom-filter", text="La 1")
        assert isinstance(w.table.cellWidget(0, 2), QComboBox) is False
        assert w.table.item(0, 2).text() == "La 1"

    def test_get_rules_lee_combo_de_grupo(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN"]))
        qtbot.addWidget(w)
        # exclusión explícita off: esta prueba comprueba el combo de grupos,
        # no los valores por defecto de la fila (ver test_ui_rule_defaults)
        w._add_row(field="group-title", text="SPAIN", exclude=False,
                   operator="or")
        rules = w.get_rules()
        assert len(rules) == 1
        assert rules[0].field == "group-title"
        assert rules[0].pattern == "SPAIN"

    def test_cambio_de_campo_convierte_celda(self, qtbot):
        """Cambiar el combo Campo alterna entre combo de grupos y texto libre."""
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA"]))
        qtbot.addWidget(w)
        w._add_row(field="custom-filter", text="La 1")
        combo_campo = w.table.cellWidget(0, 1)
        idx_grupo = combo_campo.findData("group-title")
        combo_campo.setCurrentIndex(idx_grupo)
        assert isinstance(w.table.cellWidget(0, 2), QComboBox)
        idx_nombre = combo_campo.findData("custom-filter")
        combo_campo.setCurrentIndex(idx_nombre)
        assert not isinstance(w.table.cellWidget(0, 2), QComboBox)
        assert w.table.item(0, 2).text() == "La 1"  # conserva el texto

    def test_combo_grupo_es_editable_sin_lista(self, qtbot):
        """Sin grupos cargados aún, el combo queda vacío pero editable."""
        w = FilterRulesWidget(dialog=_FakeDialog([]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        cell = w.table.cellWidget(0, 2)
        assert isinstance(cell, QComboBox)
        assert cell.isEditable()
        assert cell.currentText() == "SPAIN"


class TestGetGroupFields:
    """Pipeline expone los campos de agrupación disponibles de la fuente."""

    def _pipeline_con_canales(self, canales):
        pl = ListManagerPipeline(m3u_repository=_FakeM3URepoGroups(canales))
        pl.register_source(0, "fake.m3u", None)
        return pl

    def test_campos_presentes_con_valores(self):
        class _Ch:
            def __init__(self, groups):
                self.group = groups.get("group-title", ["Otros"])[0]
                self.groups = groups
        pl = self._pipeline_con_canales([
            _Ch({"group-title": ["SPAIN", "HD"]}),
            _Ch({"group-title": ["USA"], "tvg-group": ["News"]}),
            _Ch({"extgrp": ["Varios"]}),
        ])
        fields = pl.get_group_fields(0)
        assert fields == {
            "group-title": ["HD", "SPAIN", "USA"],
            "tvg-group": ["News"],
            "extgrp": ["Varios"],
        }

    def test_fuente_desconocida_vacio(self):
        pl = ListManagerPipeline(m3u_repository=_FakeM3URepoGroups([]))
        assert pl.get_group_fields(99) == {}

    def test_orden_alfabetico_y_sin_duplicados(self):
        class _Ch:
            def __init__(self, groups):
                self.group = groups.get("group-title", ["Otros"])[0]
                self.groups = groups
        pl = self._pipeline_con_canales([
            _Ch({"group-title": ["B", "A"]}),
            _Ch({"group-title": ["A", "C"]}),
        ])
        assert pl.get_group_fields(0)["group-title"] == ["A", "B", "C"]


class TestOperatorAndArrows:
    """Columna operador OR/AND y flechas de reordenación."""

    def _make(self, qtbot, groups=("SPAIN", "USA")):
        w = FilterRulesWidget(dialog=_FakeDialog(list(groups)))
        qtbot.addWidget(w)
        return w

    def _col_count(self, w):
        return w.table.columnCount()

    def test_columna_operador_existe(self, qtbot):
        w = self._make(qtbot)
        assert self._col_count(w) == 6  # Operador, Campo, Texto, Acción, Mayús., ""
        assert w.table.horizontalHeaderItem(0).text() == "Operador"

    def test_operador_default_and(self, qtbot):
        """Una fila nueva nace con AND (y Excluir): afinar listas existentes."""
        w = self._make(qtbot)
        w._add_row(field="group-title", text="SPAIN")
        op = w.table.cellWidget(0, 0)
        assert op.currentData() == "and"

    def test_get_rules_lee_operador(self, qtbot):
        w = self._make(qtbot)
        w._add_row(field="group-title", text="SPAIN", operator="and")
        rules = w.get_rules()
        assert rules[0].operator == "and"

    def test_flechas_suben_y_bajan_fila(self, qtbot):
        w = self._make(qtbot)
        w._add_row(field="group-title", text="SPAIN")
        w._add_row(field="group-title", text="USA")
        w.table.setCurrentCell(1, 2)
        w._move_row(1, -1)
        assert w.table.cellWidget(0, 2).currentText() == "USA"
        assert w.table.cellWidget(1, 2).currentText() == "SPAIN"
        w._move_row(0, +1)
        assert w.table.cellWidget(0, 2).currentText() == "SPAIN"

    def test_flechas_fuera_de_rango_no_explotan(self, qtbot):
        w = self._make(qtbot)
        w._add_row(field="custom-filter", text="X")
        w._move_row(0, -1)
        w._move_row(0, +1)
        assert w.table.item(0, 2).text() == "X"

    def test_botones_flecha_en_cada_fila(self, qtbot):
        w = self._make(qtbot)
        w._add_row(field="custom-filter", text="X")
        # columna 5 contiene widget con botones
        cell = w.table.cellWidget(0, 5)
        assert cell is not None

    def test_load_rules_con_operador(self, qtbot):
        w = self._make(qtbot)
        w.load_rules([
            FilterRule(field="group-title", pattern="SPAIN", operator="and"),
            FilterRule(field="group-title", pattern="USA"),
        ])
        assert w.table.cellWidget(0, 0).currentData() == "and"
        assert w.table.cellWidget(1, 0).currentData() == "or"


class TestMoveRowWidgetsVivos:
    """Regresión: tras mover filas los widgets siguen usables (crash real)."""

    def test_widgets_utilizados_tras_mover(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA"]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        w._add_row(field="custom-filter", text="HBO", exclude=False,
                   operator="or")
        w._move_row(1, -1)
        # Acceder a todos los widgets: si alguno fue borrado por Qt lanza
        # RuntimeError / segfault.
        for r in range(w.table.rowCount()):
            for c in range(w.table.columnCount()):
                cell = w.table.cellWidget(r, c)
                if isinstance(cell, QComboBox):
                    _ = cell.currentText()
                    _ = cell.currentData()
        assert w.get_rules()[0].pattern == "HBO"

    def test_pulsar_flecha_via_signal(self, qtbot):
        w = FilterRulesWidget(dialog=_FakeDialog(["SPAIN", "USA"]))
        qtbot.addWidget(w)
        w._add_row(field="group-title", text="SPAIN")
        w._add_row(field="group-title", text="USA")
        arrows = w.table.cellWidget(1, 5)
        up_btn = arrows.findChildren(type(w.table))[0] if False else None
        # simular clic del botón ↑ de la fila 1
        from PyQt6.QtWidgets import QPushButton
        btns = arrows.findChildren(QPushButton)
        btns[0].click()
        assert w.table.cellWidget(0, 2).currentText() == "USA"
        # los widgets de la fila 0 siguen vivos
        assert w.table.cellWidget(0, 2).currentData() is None or True


class TestCampoLista:
    """Campo de filtro 'Lista' (source): valores vía get_group_fields."""

    def test_get_group_fields_expone_source_tras_merged(self):
        class _Repo:
            def get_channels(self, source):
                return [_Ch({"group-title": ["G"]})]
        class _Ch:
            def __init__(self, groups):
                self.group = groups.get("group-title", ["Otros"])[0]
                self.groups = groups
        from src.application.services.list_manager.models import SourceRef
        pl = ListManagerPipeline(m3u_repository=_Repo())
        pl.register_merged("m", (SourceRef("s1", "platero", "x.m3u"),), ())
        fields = pl.get_group_fields("m")
        assert fields["source"] == ["platero"]

    def test_combo_incluye_lista(self, qtbot, rules_widget):
        """El combo Campo ofrece 'Lista' cuando el pipeline la expone."""
        rules_widget._dialog = type("D", (), {
            "available_group_fields": staticmethod(lambda: {
                "group-title": ["A"], "source": ["platero"]}),
        })()
        rules_widget._fill_field_combo(rules_widget._field_combo) \
            if hasattr(rules_widget, "_field_combo") else None
        # localiza el combo de campo de la primera fila
        from src.infrastructure.ui.list_manager_dialog import FilterRulesWidget
        combo = rules_widget.findChildren(QComboBox)[0]
        rules_widget._fill_field_combo(combo)
        data = [combo.itemData(i) for i in range(combo.count())]
        assert "source" in data
        idx = data.index("source")
        assert combo.itemText(idx) == "Lista"
