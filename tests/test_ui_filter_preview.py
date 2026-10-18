"""Vista previa de la pestaña Filtros.

La pestaña se divide 80/20: a la izquierda las reglas, a la derecha la lista
de canales que quedan al aplicar el filtro (contador + nombres), actualizada
en vivo y de solo lectura.
"""

import pytest

pytest.importorskip("PyQt6")

from src.application.services.list_manager.models import FilterRule
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.infrastructure.ui.list_manager_dialog import (
    FilterRulesWidget,
    ListManagerDialog,
)


class _Ch:
    def __init__(self, name, group="", groups=None):
        self.name = name
        self.group = group
        self.url = f"http://s/{name}"
        self.groups = groups if groups is not None else (
            {"group-title": [group]} if group else {})
        self.logo_url = None
        self.tvg_id = None


class _Repo:
    def __init__(self, channels):
        self._channels = channels
        self.calls = 0

    def get_channels(self, source):
        self.calls += 1
        return list(self._channels)


def _rule(text, field="custom-filter", exclude=False, operator="or"):
    # misma sintaxis que produce FilterRulesWidget.get_rules
    pattern = f"!{{{text}}}" if exclude else text
    return FilterRule(field=field, pattern=pattern, exclude=exclude,
                      operator=operator)


class TestPreviewFilter:
    """Etapa de filtros aislada: sin EPG ni canales desactivados."""

    def _pipeline(self, channels):
        pl = ListManagerPipeline(m3u_repository=_Repo(channels))
        pl.register_source(0, "fake.m3u", None)
        return pl

    def test_sin_reglas_devuelve_todos(self):
        pl = self._pipeline([_Ch("A"), _Ch("B")])
        kept, total = pl.preview_filter(0, [])
        assert [c.name for c in kept] == ["A", "B"]
        assert total == 2

    def test_regla_de_nombre_incluye(self):
        pl = self._pipeline([_Ch("La 1"), _Ch("La 2"), _Ch("Antena 3")])
        kept, total = pl.preview_filter(0, [_rule("La")])
        assert [c.name for c in kept] == ["La 1", "La 2"]
        assert total == 3

    def test_regla_de_nombre_excluye(self):
        pl = self._pipeline([_Ch("La 1"), _Ch("La 2")])
        kept, _ = pl.preview_filter(0, [_rule("La 2", exclude=True)])
        assert [c.name for c in kept] == ["La 1"]

    def test_regla_de_grupo(self):
        pl = self._pipeline([_Ch("A", "Cine"), _Ch("B", "Deportes")])
        kept, _ = pl.preview_filter(0, [_rule("Cine", field="group-title")])
        assert [c.name for c in kept] == ["A"]

    def test_separadores_de_seccion_fuera(self):
        pl = self._pipeline([_Ch("### TOP ###"), _Ch("Canal")])
        kept, total = pl.preview_filter(0, [])
        assert [c.name for c in kept] == ["Canal"]
        assert total == 2

    def test_fuente_no_registrada_vacio(self):
        pl = ListManagerPipeline(m3u_repository=_Repo([_Ch("A")]))
        assert pl.preview_filter("nada", []) == ([], 0)

    def test_reutiliza_la_cache_de_canales(self):
        repo = _Repo([_Ch("A")])
        pl = ListManagerPipeline(m3u_repository=repo)
        pl.register_source(0, "fake.m3u", None)
        pl.preview_filter(0, [])
        pl.preview_filter(0, [])
        assert repo.calls == 1


class TestRulesChangedSignal:
    """Cualquier cambio en las reglas avisa para refrescar la vista previa."""

    def _widget(self, qtbot):
        w = FilterRulesWidget()
        qtbot.addWidget(w)
        return w

    def test_anadir_fila_emite(self, qtbot):
        w = self._widget(qtbot)
        with qtbot.waitSignal(w.rules_changed, timeout=1000):
            w._add_row()

    def test_editar_texto_emite(self, qtbot):
        w = self._widget(qtbot)
        w._add_row()
        with qtbot.waitSignal(w.rules_changed, timeout=1000):
            w.table.item(0, 2).setText("Cine")

    def test_cambiar_operador_emite(self, qtbot):
        w = self._widget(qtbot)
        w._add_row()
        with qtbot.waitSignal(w.rules_changed, timeout=1000):
            w.table.cellWidget(0, 0).setCurrentIndex(0)  # AND -> OR

    def test_quitar_fila_emite(self, qtbot):
        w = self._widget(qtbot)
        w._add_row()
        w.table.setCurrentCell(0, 2)
        with qtbot.waitSignal(w.rules_changed, timeout=1000):
            w._remove_row()

    def test_load_rules_no_dispara_una_por_fila(self, qtbot):
        """Cargar N reglas avisa una sola vez (evita N previews al arrancar)."""
        w = self._widget(qtbot)
        emitted = []
        w.rules_changed.connect(lambda: emitted.append(1))
        w.load_rules([
            _rule("A"), _rule("B"), _rule("C"),
        ])
        assert len(emitted) == 1


@pytest.fixture
def dialog(qtbot, tmp_path, monkeypatch):
    from src.infrastructure.adapters import file_m3u_repository as repo

    monkeypatch.setattr(
        repo.FileM3URepository, "_get_content",
        lambda self, source: [
            "#EXTM3U",
            '#EXTINF:-1 tvg-id="a" group-title="Cine",Canal A',
            "http://st/a",
            '#EXTINF:-1 tvg-id="b" group-title="Deportes",Canal B',
            "http://st/b",
        ])
    cfg = tmp_path / "config.ini"
    cfg.write_text("[listmanager.target.0]\nname = x\nport = 8080\n",
                   encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x", "port": 8080,
    })
    qtbot.addWidget(d)
    return d


class TestPreviewPanel:
    def test_panel_a_la_derecha_de_las_reglas(self, dialog):
        assert dialog.preview_list is not None
        assert dialog.preview_count_label is not None
        # misma pestaña que las reglas
        assert dialog.preview_list.parent() is not None

    def test_solo_lectura(self, dialog):
        from PyQt6.QtWidgets import QAbstractItemView

        assert dialog.preview_list.editTriggers() == \
            QAbstractItemView.EditTrigger.NoEditTriggers
        assert dialog.preview_list.selectionMode() == \
            QAbstractItemView.SelectionMode.NoSelection

    def test_rellena_con_la_respuesta_del_daemon(self, dialog, fake_api):
        fake_api.preview_names = ["Canal A", "Canal B"]
        fake_api.preview_total = 2
        dialog._refresh_filter_preview()
        assert dialog.preview_list.count() == 2
        assert dialog.preview_count_label.text() == "2 de 2 canales"

    def test_pide_la_vista_previa_con_las_reglas_actuales(self, dialog, qtbot,
                                                         fake_api):
        fake_api.preview_names = ["Canal A"]
        fake_api.preview_total = 2
        dialog.rules_widget._add_row(field="custom-filter", text="Canal A",
                                     exclude=False, operator="or")
        qtbot.waitUntil(lambda: dialog.preview_list.count() == 1, timeout=2000)
        assert dialog.preview_list.item(0).text() == "Canal A"
        assert dialog.preview_count_label.text() == "1 de 2 canales"
        assert fake_api.calls[-1] == ("preview", ["custom-filter:Canal A:0"])

    def test_sin_fuente_no_lista_nada(self, dialog, fake_api):
        dialog._refresh_filter_preview()
        assert dialog.preview_list.count() == 0
        assert dialog.preview_count_label.text() == "Sin fuente cargada"

    def test_daemon_no_disponible_lo_dice(self, dialog, fake_api):
        fake_api.down = True
        dialog._refresh_filter_preview()
        assert dialog.preview_count_label.text() == "Demonio no disponible"
