"""Pestañas del diálogo: Estado / Listas IPTV / Filtros iptv / Listas EPG /
Mapeado IPTV <-> EPG / Configuración / Logs."""
from pathlib import Path

import pytest

from src.infrastructure.ui.list_manager_dialog import ListManagerDialog


@pytest.fixture
def dialog(qtbot):
    d = ListManagerDialog(parent=None, config={"target_id": "0", "_config_path": Path("config.ini")})
    qtbot.addWidget(d)
    return d


class TestTabs:
    def test_tab_widget_existe(self, dialog):
        assert dialog.tabs is not None
        assert dialog.tabs.count() == 8

    def test_titulos_de_pestanas(self, dialog):
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert titles == [
            "Estado", "Listas IPTV", "Filtros iptv", "Listas EPG",
            "Mapeado IPTV <-> EPG", "Catálogo de la guía", "Configuración",
            "Logs",
        ]

    def test_widgets_dentro_de_pestanas(self, dialog):
        # el contenido sigue accesible por los mismos atributos
        def _ancestor(w, tab):
            p = w.parent()
            while p is not None:
                if p is tab or (hasattr(p, "widget") and p.widget() is tab):
                    return True
                p = p.parent()
            return False

        assert _ancestor(dialog.name_edit, dialog.tab_config)
        assert _ancestor(dialog.rules_widget, dialog.tab_filters)
        assert _ancestor(dialog.epg_mapping, dialog.tab_mapping)

    def test_carteras_son_widgets_de_cartera(self, dialog):
        from src.infrastructure.ui.source_catalog_widget import SourceCatalogWidget
        assert isinstance(dialog.catalog_m3u_widget, SourceCatalogWidget)
        assert isinstance(dialog.catalog_epg_widget, SourceCatalogWidget)


class TestEpgMappingTable:
    """Tabla de mapeo manual: combo EPG por fila, overrides y activación."""

    def _widget(self, qtbot, dialog):
        w = dialog.epg_mapping
        qtbot.addWidget(w)
        return w

    @staticmethod
    def _epg():
        from types import SimpleNamespace
        return SimpleNamespace(
            channel_names={"es.la1": "La 1", "es.clan": "Clan"},
            programs_by_channel_ids=lambda: ["es.la1", "es.clan"])

    @staticmethod
    def _channels():
        from src.application.services.list_manager.models import MappedChannel
        return [
            MappedChannel("La 1", "http://s/la1"),
            MappedChannel("Raro", "http://s/raro"),
        ]

    def test_combo_se_llena_con_los_canales_del_epg(self, qtbot, dialog):
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        texts = combo._combo.ensure_items() or [combo._combo.itemText(i)
                                               for i in range(combo._combo.count())]
        assert "La 1" in texts and "es.la1" in texts
        assert "Clan" in texts and "es.clan" in texts

    def test_combo_sin_epg_ofrece_blanco_y_dummy(self, qtbot, dialog):
        """Sin guía no hay canales que listar, pero 'Dummy 1h' sigue disponible:
        es la única forma de rescatar un canal en ese estado."""
        from src.application.services.list_manager.dummy_epg import DUMMY_LABEL

        w = self._widget(qtbot, dialog)
        w.set_epg(None)
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        assert combo._combo.ensure_items() or [combo._combo.itemText(i)
                                               for i in range(combo._combo.count())] ==             ["", DUMMY_LABEL]
        assert combo.current_value() == ""

    def test_override_persistido_se_muestra(self, qtbot, dialog):
        """config.ini guarda el channel-id; lo que se ve es su nombre."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {"http://s/raro": "es.clan"})
        combo = w.table.cellWidget(1, w.COL_EPG)
        assert combo.current_value() == "Clan"

    def test_get_overrides_resuelve_nombre_a_id(self, qtbot, dialog):
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {"http://s/raro": "es.clan"})
        assert w.get_overrides() == {"http://s/raro": "es.clan"}

    def test_nombre_mostrado_se_resuelve_a_su_id(self, qtbot, dialog):
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        combo._combo.setCurrentText("Clan")
        assert w.get_overrides() == {"http://s/la1": "es.clan"}

    def test_sin_epg_asignado_no_se_desactiva(self, qtbot, dialog):
        """Un canal sin EPG se sirve con guía de relleno: no se auto-desactiva."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {})
        assert w.get_disabled() == ()

    @staticmethod
    def _epg_con_listas():
        from types import SimpleNamespace
        return SimpleNamespace(
            channel_names={"es.la1": "La 1", "es.clan": "Clan"},
            epg_lists={"es.la1": ["guia-1"], "es.clan": ["guia-1", "guia-2"]})

    def test_desplegable_dice_de_que_guia_viene_cada_canal(self, qtbot, dialog):
        """Con varias guías el nombre del canal solo no identifica la fuente."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg_con_listas())
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        texts = combo._combo.ensure_items() or [combo._combo.itemText(i)
                                               for i in range(combo._combo.count())]
        assert "La 1 · guia-1" in texts
        assert "Clan · guia-1 + guia-2" in texts

    def test_override_persistido_se_ve_con_su_lista(self, qtbot, dialog):
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg_con_listas())
        w.load_unmatched(self._channels(), {"http://s/raro": "es.clan"})
        combo = w.table.cellWidget(1, w.COL_EPG)
        assert combo.current_value() == "Clan · guia-1 + guia-2"

    def test_el_sufijo_no_rompe_la_asignacion(self, qtbot, dialog):
        """La etiqueta visible lleva sufijo; el fichero guarda el channel-id."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg_con_listas())
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        combo._combo.setCurrentText("Clan · guia-1 + guia-2")
        assert w.get_overrides() == {"http://s/la1": "es.clan"}

    def test_sin_datos_de_lista_no_hay_sufijo(self, qtbot, dialog):
        """Fuente de una sola guía sin nombre: etiqueta limpia como antes."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {})
        combo = w.table.cellWidget(0, w.COL_EPG)
        texts = combo._combo.ensure_items() or [combo._combo.itemText(i)
                                               for i in range(combo._combo.count())]
        assert not any(" · " in t for t in texts)

    def test_desactivado_persistido_sigue_listado(self, qtbot, dialog):
        """Un canal desactivado sigue en la tabla para poder reactivarlo."""
        w = self._widget(qtbot, dialog)
        w.set_epg(self._epg())
        w.load_unmatched(self._channels(), {}, disabled=("http://s/raro",))
        assert w.table.rowCount() == 2
        assert w.get_disabled() == ("http://s/raro",)


class TestVentanaYColumnas:
    """Ventana usada como principal y tabla de mapeo ajustable a mano."""

    def test_la_barra_de_titulo_permite_minimizar_y_maximizar(self, dialog):
        from PyQt6.QtCore import Qt

        flags = dialog.windowFlags()
        assert flags & Qt.WindowType.WindowMinimizeButtonHint
        assert flags & Qt.WindowType.WindowMaximizeButtonHint

    def test_las_columnas_del_mapeado_son_redimensionables(self, dialog):
        from PyQt6.QtWidgets import QHeaderView

        w = dialog.epg_mapping
        header = w.table.horizontalHeader()
        for columna in (w.COL_NAME, w.COL_LOGO, w.COL_EPG, w.COL_ACTIVE):
            assert (header.sectionResizeMode(columna)
                    == QHeaderView.ResizeMode.Interactive)

    def test_se_puede_cambiar_el_ancho_de_una_columna(self, dialog):
        w = dialog.epg_mapping
        antes = w.table.columnWidth(w.COL_EPG)
        w.table.setColumnWidth(w.COL_EPG, antes + 60)
        assert w.table.columnWidth(w.COL_EPG) == antes + 60
