"""Configuración: selector de target y prioridad de calidad por target.

El target activo manda en toda la ventana (reglas, cartera, mapeo) y la
prioridad de calidad se guarda con él en ``daemon.toml``.
"""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from toml_helpers import source, target, write_client, write_daemon

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    write_daemon(
        tmp_path / "daemon.toml",
        sources=[source("s1", "lista", "m3u", "C:/l.m3u")],
        targets=[
            target("0", name="casa", custom_m3u="C:/l.m3u",
                   m3u_sources=["s1"],
                   rules=["group-title:SPAIN:0:and"],
                   quality_priority={"full hd": 5, "sd": 1}),
            target("1", name="movil", custom_m3u="C:/movil.m3u",
                   rules=["group-title:Cine:0:or"]),
        ])
    write_client(tmp_path / "weteve.ini", control_port=32123)
    return tmp_path / "daemon.toml"


def _dialog(qtbot, path: Path, target_id: str = "0") -> ListManagerDialog:
    config = {"target_id": target_id, "_config_path": path,
              "_client_path": path.with_name("weteve.ini"), "name": "casa",
              "port": 8080, "custom_m3u": "C:/l.m3u"}
    d = ListManagerDialog(None, config=config)
    qtbot.addWidget(d)
    d._config_watch.stop()
    return d


class TestSelectorDeTarget:
    def test_el_combo_lista_los_targets_del_demonio(self, qtbot, config_path,
                                                    fake_api):
        fake_api.targets_map = {"0": "casa", "1": "movil"}
        d = _dialog(qtbot, config_path)
        etiquetas = [d.target_combo.itemText(i)
                     for i in range(d.target_combo.count())]
        assert "0 — casa" in etiquetas and "1 — movil" in etiquetas
        assert d.target_combo.currentData() == "0"

    def test_cambiar_de_target_rebinda_la_ventana(self, qtbot, config_path,
                                                  fake_api):
        fake_api.targets_map = {"0": "casa", "1": "movil"}
        d = _dialog(qtbot, config_path)
        d.target_combo.setCurrentIndex(d.target_combo.findData("1"))
        assert d._target_id() == "1"
        assert d.name_edit.text() == "movil"
        # las reglas del target nuevo, no las del anterior
        reglas = [r.serialize() for r in d.rules_widget.get_rules()]
        assert reglas == ["group-title:Cine:0"]  #

    def test_al_cambiar_muestra_las_prioridades_del_target(self, qtbot,
                                                           config_path,
                                                           fake_api):
        fake_api.targets_map = {"0": "casa", "1": "movil"}
        d = _dialog(qtbot, config_path)
        assert d.priority_widget.values()["full hd"] == 5
        d.target_combo.setCurrentIndex(d.target_combo.findData("1"))
        # sin prioridad configurada: se muestran las de fábrica
        from src.application.services.list_manager.quality import DEFAULT_PRIORITY

        assert d.priority_widget.values() == DEFAULT_PRIORITY


class TestPrioridadDeCalidad:
    def test_el_guardado_manda_la_tabla(self, qtbot, config_path, fake_api,
                                        wait_save):
        d = _dialog(qtbot, config_path)
        tabla = d.priority_widget.table
        tabla.item(0, d.priority_widget.COL_PRIORIDAD).setText("0")
        d._on_save()
        wait_save(d)
        puts = {c[1]: c[2] for c in fake_api.calls if c[0] == "put"}
        enviado = puts["/targets/0/settings"]["quality_priority"]
        assert enviado["full hd"] == 0
        assert enviado["sd"] == 1

    def test_restaurar_vuelve_a_las_de_fabrica(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        d.priority_widget.reset()
        from src.application.services.list_manager.quality import DEFAULT_PRIORITY

        assert d.priority_widget.values() == DEFAULT_PRIORITY

    def test_se_puede_añadir_un_token_nuevo(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        d.priority_widget._add_row("h265", 6)
        assert d.priority_widget.values()["h265"] == 6

    def test_las_prioridades_quedan_en_el_rango_0_9(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        tabla = d.priority_widget.table
        tabla.item(0, d.priority_widget.COL_PRIORIDAD).setText("99")
        assert d.priority_widget.values()["full hd"] == 9


class TestEliminarCalidad:
    """Botón «Eliminar calidad» y explicación del sentido del número."""

    def test_el_boton_existe(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        assert d.priority_widget.remove_btn.text() == "&Eliminar calidad"

    def test_elimina_la_fila_seleccionada(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        w = d.priority_widget
        antes = dict(w.values())
        assert "full hd" in antes
        fila = next(r for r in range(w.table.rowCount())
                    if w.table.item(r, w.COL_CALIDAD).text() == "full hd")
        w.table.selectRow(fila)
        w.remove_btn.click()
        assert "full hd" not in w.values()
        assert len(w.values()) == len(antes) - 1

    def test_sin_seleccion_no_cambia_nada(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        w = d.priority_widget
        antes = dict(w.values())
        w.remove_btn.click()
        assert w.values() == antes

    def test_la_explicacion_dice_que_mas_es_mejor(self, qtbot, config_path):
        d = _dialog(qtbot, config_path)
        texto = d.priority_widget.hint_label.text()
        assert "Más número = más prioridad" in texto
        assert "0 = no servir nunca" in texto
        assert "1 = último recurso" in texto
