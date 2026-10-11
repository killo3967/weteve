"""Regresión: al abrir el diálogo la fuente ya debe estar resuelta.

Se perdió la llamada final a _update_source_mode() en el __init__ y con ella
_refresh_mapping(): el pipeline nunca cargaba la lista y el desplegable de
grupos de las reglas salía vacío.
"""
import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog
from src.application.services.list_manager.models import FilterRule
from pathlib import Path

M3U = """#EXTM3U
#EXTINF:-1 tvg-id="a" group-title="DEPORTES",Canal Deporte
http://x/a.ts
#EXTINF:-1 tvg-id="b" group-title="CINE",Canal Cine
http://x/b.ts
#EXTINF:-1 tvg-id="c" group-title="INFANTIL",Canal Infantil
http://x/c.ts
"""


@pytest.fixture
def dialog(qtbot, tmp_path):
    m3u = tmp_path / "lista.m3u"
    m3u.write_text(M3U, encoding="utf-8")
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "custom_m3u": str(m3u).replace("\\", "/"), "rules": [], "port": 8080,
    })
    qtbot.addWidget(d)
    return d


class TestArranqueConFuente:
    def test_fuente_resuelta_al_abrir(self, dialog):
        assert dialog._resolve_source_path() is not None

    def test_pipeline_registra_la_fuente(self, dialog):
        assert list(dialog._pipeline._sources) == [dialog._resolve_source_path()]

    def test_groups_disponibles_al_abrir(self, dialog):
        assert dialog.available_groups() == ["CINE", "DEPORTES", "INFANTIL"]

    def test_group_fields_pueblan_el_combo_de_campo(self, dialog):
        dialog.rules_widget._add_row()
        combo = dialog.rules_widget.table.cellWidget(0, 1)
        assert [combo.itemData(i) for i in range(combo.count())] == \
               ["custom-filter", "group-title"]

    def test_combo_de_grupo_relleno_al_anadir_fila(self, dialog):
        dialog.rules_widget._add_row(field="group-title", text="")
        cell = dialog.rules_widget.table.cellWidget(0, 2)
        assert [cell.itemText(i) for i in range(cell.count())] == \
               ["CINE", "DEPORTES", "INFANTIL"]

    def test_result_label_muestra_resumen(self, dialog):
        assert "3 canales" in dialog.result_label.text()


class TestRepobladoTrasCargarFuente:
    """Las reglas se construyen antes de resolver la fuente: los desplegables
    de grupo deben rellenarse cuando la lista ya está cargada."""

    def test_fila_grupo_cargada_desde_config_queda_rellena(self, dialog):
        from src.application.services.list_manager.models import FilterRule
        dialog.rules_widget.load_rules([
            FilterRule(field="group-title", pattern="INFANTIL")])
        assert dialog.rules_widget.table.cellWidget(0, 2).count() == 3

    def test_repoblado_conserva_el_valor_de_la_regla(self, dialog):
        from src.application.services.list_manager.models import FilterRule
        dialog.rules_widget.load_rules([
            FilterRule(field="group-title", pattern="CINE")])
        cell = dialog.rules_widget.table.cellWidget(0, 2)
        assert cell.currentText() == "CINE"
        assert dialog.rules_widget.get_rules()[0].pattern == "CINE"

    def test_repoblado_no_toca_las_filas_de_nombre(self, dialog):
        from src.application.services.list_manager.models import FilterRule
        dialog.rules_widget.load_rules([
            FilterRule(field="group-title", pattern="CINE"),
            FilterRule(field="custom-filter", pattern="La 1")])
        assert dialog.rules_widget.table.item(1, 2).text() == "La 1"


class TestArranqueConReglaDeGrupo:
    """Orden real de __init__: las reglas se construyen ANTES de resolver la
    fuente, así que deben refrescarse cuando los grupos ya existen."""

    @pytest.fixture
    def dialog_con_regla_grupo(self, qtbot, tmp_path):
        m3u = tmp_path / "lista.m3u"
        m3u.write_text(M3U, encoding="utf-8")
        cfg = tmp_path / "config.ini"
        cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg, "name": "x",
            "custom_m3u": str(m3u).replace("\\", "/"), "port": 8080,
            "rules": [FilterRule.parse("group-title:CINE:0")],
        })
        qtbot.addWidget(d)
        return d

    def test_fila_grupo_traida_de_config_queda_rellena(self, dialog_con_regla_grupo):
        cell = dialog_con_regla_grupo.rules_widget.table.cellWidget(0, 2)
        assert [cell.itemText(i) for i in range(cell.count())] == \
               ["CINE", "DEPORTES", "INFANTIL"]

    def test_y_conserva_su_valor(self, dialog_con_regla_grupo):
        assert dialog_con_regla_grupo.rules_widget.get_rules()[0].pattern == "CINE"


class TestCambioDeOrigenRefrescaGrupos:
    """Cambiar la ruta del M3U debe volver a poblar los desplegables."""

    @pytest.fixture
    def dialog_vacio(self, qtbot, tmp_path):
        cfg = tmp_path / "config.ini"
        cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
        d = ListManagerDialog(None, config={
            "target_id": "0", "_config_path": cfg, "name": "x",
            "rules": [], "port": 8080})
        qtbot.addWidget(d)
        return d

    def test_sin_fuente_los_grupos_de_fila_estan_vacios(self, dialog_vacio):
        dialog_vacio.rules_widget._add_row(field="group-title", text="")
        assert dialog_vacio.rules_widget.table.cellWidget(0, 2).count() == 0

    def test_al_indicar_la_ruta_se_pueblan(self, dialog_vacio, tmp_path):
        m3u = tmp_path / "otra.m3u"
        m3u.write_text(M3U, encoding="utf-8")
        dialog_vacio.rules_widget._add_row(field="group-title", text="")
        dialog_vacio.custom_m3u_edit.setText(str(m3u).replace("\\", "/"))
        dialog_vacio.custom_m3u_edit.editingFinished.emit()
        cell = dialog_vacio.rules_widget.table.cellWidget(0, 2)
        assert [cell.itemText(i) for i in range(cell.count())] == \
               ["CINE", "DEPORTES", "INFANTIL"]

    def test_repoblado_no_pierde_la_seleccion(self, dialog_vacio, tmp_path):
        m3u = tmp_path / "otra.m3u"
        m3u.write_text(M3U, encoding="utf-8")
        dialog_vacio.rules_widget._add_row(field="group-title", text="CINE",
                                           exclude=False, operator="or")
        dialog_vacio.custom_m3u_edit.setText(str(m3u).replace("\\", "/"))
        dialog_vacio.custom_m3u_edit.editingFinished.emit()
        assert dialog_vacio.rules_widget.get_rules()[0].pattern == "CINE"


class TestGruposDisponiblesEnUI:
    """El síntoma del usuario: "selecciono Grupo y no sale la lista"."""

    def test_campo_grupo_expone_los_grupos_reales(self, dialog):
        dialog.rules_widget._add_row()
        w = dialog.rules_widget
        campo = w.table.cellWidget(0, 1)
        campo.setCurrentIndex(campo.findData("group-title"))
        texto = w.table.cellWidget(0, 2)
        assert texto.count() == 3
        assert texto.currentText() == "CINE"

    def test_boton_grupos_abre_el_menu_con_los_grupos(self, dialog):
        """"Grupos…" ya no avisa de que no hay grupos cargados."""
        pedidos = {}
        dialog.rules_widget._choose_group = lambda groups: pedidos.setdefault(
            "groups", list(groups)) and None
        dialog.rules_widget._warn_no_groups = lambda: pedidos.update(
            warned=True)
        dialog.rules_widget._pick_group()
        assert pedidos.get("warned") is None
        assert pedidos["groups"] == ["CINE", "DEPORTES", "INFANTIL"]
