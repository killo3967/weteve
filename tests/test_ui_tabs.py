"""Pestañas del diálogo: Orígenes / Filtros / Mapeado en QTabWidget."""
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
        assert dialog.tabs.count() == 3

    def test_titulos_de_pestanas(self, dialog):
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert titles == ["Orígenes", "Filtros", "Mapeado EPG"]

    def test_widgets_dentro_de_pestanas(self, dialog):
        # el contenido sigue accesible por los mismos atributos
        def _ancestor(w, tab):
            p = w.parent()
            while p is not None:
                if p is tab or (hasattr(p, "widget") and p.widget() is tab):
                    return True
                p = p.parent()
            return False

        assert _ancestor(dialog.name_edit, dialog.tab_sources)
        assert _ancestor(dialog.rules_widget, dialog.tab_filters)
        assert _ancestor(dialog.epg_mapping, dialog.tab_mapping)
