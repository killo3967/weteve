"""Tabla de mapeo: la GUI pinta lo que le da el daemon.

El invariante "los canales que necesitan atención siguen listados" (incluidos
los descartados sin guía y los desactivados) vive ahora en
``TargetSupervisor.mapping_candidates``; aquí se comprueba que el diálogo lo
pinta, con su catálogo de guía y su resumen.
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QTableWidgetSelectionRange

from src.infrastructure.ui.list_manager_dialog import (
    MAPPING_PAGE,
    ListManagerDialog,
)


@pytest.fixture
def dialog(qtbot, tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[DAEMON]\ncontrol_port = 32123\n", encoding="utf-8")
    d = ListManagerDialog(None, config={
        "target_id": "0", "_config_path": cfg, "name": "x",
        "custom_m3u": "C:/lista.m3u", "rules": [], "port": 8080})
    qtbot.addWidget(d)
    return d


def _row(name="Antena 3 Internacional", url="http://x/a3int.ts",
         tvg_id="", disabled=False, logo=""):
    return {"name": name, "url": url, "group": "G", "tvg_id": tvg_id,
            "logo_url": logo, "disabled": disabled}


def _rows(dialog):
    table = dialog.epg_mapping.table
    columna = dialog.epg_mapping.COL_NAME
    return [table.item(r, columna).text() for r in range(table.rowCount())]


class TestFilasDeLaTabla:
    def test_las_filas_son_los_candidatos_del_daemon(self, dialog, fake_api):
        fake_api.served_channels = [_row()]
        dialog._refresh_mapping()
        assert _rows(dialog) == ["Antena 3 Internacional"]

    def test_un_desactivado_sigue_listado(self, dialog, fake_api):
        fake_api.served_channels = [_row(name="La 1", url="http://x/la1.ts",
                                         disabled=True)]
        dialog._refresh_mapping()
        assert _rows(dialog) == ["La 1"]
        assert dialog.epg_mapping.get_disabled() == ("http://x/la1.ts",)

    def test_el_catalogo_de_guia_llena_el_desplegable(self, dialog, fake_api):
        fake_api.served_channels = [_row()]
        fake_api.epg = [{"id": "es.la1", "display_name": "La 1"}]
        dialog._refresh_mapping()
        combo = dialog.epg_mapping.table.cellWidget(0, dialog.epg_mapping.COL_EPG)
        combo.ensure_items()
        assert "La 1" in [combo._combo.itemText(i)
                          for i in range(combo._combo.count())]

    def test_la_url_del_logo_llega_a_la_fila(self, dialog, fake_api):
        fake_api.served_channels = [_row(logo="http://x/logo.png")]
        dialog._refresh_mapping()
        item = dialog.epg_mapping.table.item(0, dialog.epg_mapping.COL_LOGO)
        assert item.data(0x0100) == "http://x/logo.png"  # UserRole


class TestResumen:
    def test_cuenta_servidos_y_pendientes(self, dialog, fake_api):
        fake_api.served = 5
        fake_api.served_channels = [_row(), _row(name="Otra", url="http://y")]
        dialog._refresh_mapping()
        text = dialog.result_label.text()
        assert "5 canales servidos" in text
        assert "2 necesitan atención" in text

    def test_nada_pendiente(self, dialog, fake_api):
        dialog._refresh_mapping()
        assert "ninguno necesita atención" in dialog.result_label.text()


class TestSinDaemon:
    def test_tabla_vacia_y_aviso(self, dialog, fake_api):
        fake_api.down = True
        dialog._refresh_mapping()
        assert dialog.epg_mapping.table.rowCount() == 0
        assert "no responde" in dialog.result_label.text()


class TestRevisionDelDemonio:
    """La tabla se repinta por revisión, no por fecha del fichero."""

    class _Cliente:
        def __init__(self, revision: int) -> None:
            self.revision = revision

        def status(self):
            return 200, {"targets": {"0": {"revision": self.revision}}}

    def test_solo_repinta_cuando_cambia_la_revision(self, dialog):
        cliente = self._Cliente(1)
        dialog._api_client = lambda: cliente
        refrescos: list[int] = []
        dialog._refresh_mapping = lambda: refrescos.append(1)

        dialog._revisar_revision()
        assert refrescos == [1], "la primera revisión se pinta"
        dialog._revisar_revision()
        assert refrescos == [1], "misma revisión: no se pide la tabla"

        cliente.revision = 2
        dialog._revisar_revision()
        assert refrescos == [1, 1], "revisión nueva: se repinta"


class TestCanalesYaServidos:
    """La tabla incluye los canales que ya funcionan para poder revisarlos."""

    def _servido(self):
        return _row(name="LA 1 HD", url="http://x/la1.ts", tvg_id="La 1 HD")

    def test_por_defecto_se_piden_tambien_los_servidos(self, dialog, fake_api):
        fake_api.served_channels = [self._servido()]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD",
                         "lists": ["guia-1"]}]
        dialog._refresh_mapping()
        assert fake_api.last_include_served is True
        assert _rows(dialog) == ["LA 1 HD"]
        combo = dialog.epg_mapping.table.cellWidget(
            0, dialog.epg_mapping.COL_EPG)
        # se ve la guía efectiva, no una celda vacía
        assert combo.current_value() == "La 1 HD · guia-1"

    def test_la_casilla_filtra_los_que_ya_sirven(self, dialog, fake_api):
        fake_api.served_channels = [self._servido()]
        dialog._refresh_mapping()
        dialog.epg_mapping.only_pending_check.setChecked(True)
        assert fake_api.last_include_served is False

    def test_la_guia_automatica_no_se_convierte_en_mapeo_manual(
            self, dialog, fake_api):
        """Si el usuario no toca el desplegable, no se escriben overrides."""
        fake_api.served_channels = [self._servido()]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD"}]
        dialog._refresh_mapping()
        assert dialog.epg_mapping.get_overrides() == {}

    def test_cambiar_la_guia_de_un_canal_servido_si_es_mapeo(
            self, dialog, fake_api):
        fake_api.served_channels = [self._servido()]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD"},
                        {"id": "La 1", "display_name": "La 1"}]
        dialog._refresh_mapping()
        combo = dialog.epg_mapping.table.cellWidget(
            0, dialog.epg_mapping.COL_EPG)
        combo._combo.setCurrentText("La 1")
        assert dialog.epg_mapping.get_overrides() == {"http://x/la1.ts": "La 1"}


class TestSeleccionMultipleYMenu:
    """Selección por filas (Ctrl/Shift) y menú contextual habilitar/deshabilitar."""

    def _filas(self, fake_api):
        fake_api.served_channels = [
            _row(name="LA 1 HD", url="http://x/la1.ts", tvg_id="La 1 HD"),
            _row(name="LA 2 HD", url="http://x/la2.ts", tvg_id="La 2"),
            _row(name="SIN GUIA", url="http://x/sin.ts", tvg_id=""),
        ]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD"},
                        {"id": "La 2", "display_name": "La 2"}]

    def test_la_tabla_permite_seleccionar_varias_filas(self, dialog):
        tabla = dialog.epg_mapping.table
        assert (tabla.selectionBehavior()
                == QAbstractItemView.SelectionBehavior.SelectRows)
        assert (tabla.selectionMode()
                == QAbstractItemView.SelectionMode.ExtendedSelection)

    def test_el_menu_contextual_esta_activo(self, dialog):
        assert (dialog.epg_mapping.table.contextMenuPolicy()
                == Qt.ContextMenuPolicy.CustomContextMenu)

    def test_filas_seleccionadas(self, dialog, fake_api):
        self._filas(fake_api)
        dialog._refresh_mapping()
        tabla = dialog.epg_mapping.table
        ultima = tabla.columnCount() - 1
        tabla.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, ultima), True)
        tabla.setRangeSelected(QTableWidgetSelectionRange(2, 0, 2, ultima), True)
        assert dialog.epg_mapping.selected_rows() == [0, 2]

    def test_deshabilitar_desde_el_menu(self, dialog, fake_api):
        self._filas(fake_api)
        dialog._refresh_mapping()
        w = dialog.epg_mapping
        menu = w.row_menu([0, 1])
        textos = [a.text() for a in menu.actions()]
        assert {"Habilitar", "Deshabilitar"} <= set(textos)
        next(a for a in menu.actions() if a.text() == "Deshabilitar").trigger()
        assert w.get_disabled() == ("http://x/la1.ts", "http://x/la2.ts")
        # las casillas 'Activo' acompañan
        for row in (0, 1):
            item = w.table.item(row, w.COL_ACTIVE)
            assert item.checkState() == Qt.CheckState.Unchecked

    def test_habilitar_desde_el_menu(self, dialog, fake_api):
        self._filas(fake_api)
        dialog._refresh_mapping()
        w = dialog.epg_mapping
        w.set_rows_enabled([0, 1], False)
        assert len(w.get_disabled()) == 2
        w.set_rows_enabled([0, 1], True)
        assert w.get_disabled() == ()
        assert (w.table.item(0, w.COL_ACTIVE).checkState()
                == Qt.CheckState.Checked)

    def test_deshabilitar_una_fila_sin_guia_no_se_persiste(self, dialog,
                                                           fake_api):
        """Sin guía ya está fuera: apagarla no es una decisión del usuario."""
        self._filas(fake_api)
        dialog._refresh_mapping()
        dialog.epg_mapping.set_rows_enabled([2], False)
        assert dialog.epg_mapping.get_disabled() == ()

    def test_clic_derecho_fuera_de_la_seleccion_opera_sobre_esa_fila(
            self, dialog, fake_api, monkeypatch):
        self._filas(fake_api)
        dialog._refresh_mapping()
        w = dialog.epg_mapping
        capturado = {}

        class _Dummy:
            def exec(self, *_a):
                return None

        monkeypatch.setattr(w, "row_menu",
                            lambda rows: capturado.update(rows=rows) or _Dummy())

        class _Index:
            def isValid(self):
                return True

            def row(self):
                return 2

            def column(self):
                return w.COL_NAME      # una columna de fila, no la del logo

        monkeypatch.setattr(w.table, "indexAt", lambda pos: _Index())
        w._show_row_menu(w.table.rect().center())
        assert capturado["rows"] == [2]


class TestCatalogoIncompleto:
    """Si el demonio no entrega todo, la GUI lo dice en vez de callarse."""

    def test_avisa_si_el_demonio_recorta_la_pagina(self, dialog, fake_api,
                                                   monkeypatch):
        fake_api.served_channels = [_row()]
        original = fake_api.mapping_candidates

        def recortado(target_id, limit=1000, offset=0, include_served=True):
            code, body = original(target_id, limit=limit, offset=offset,
                                  include_served=include_served)
            body["total"] = 10        # hay 10 canales, pero solo llega uno
            body["truncated"] = True
            return code, body

        monkeypatch.setattr(fake_api, "mapping_candidates", recortado)
        dialog._refresh_mapping()
        assert "catálogo incompleto" in dialog.result_label.text()

    def test_una_pagina_con_mas_filas_no_es_catalogo_incompleto(
            self, dialog, fake_api):
        """Que queden filas es paginación: las trae «Cargar más»."""
        fake_api.served_channels = [_row(name=f"Canal {i}", url=f"http://x/{i}")
                                    for i in range(80)]
        dialog._refresh_mapping()
        assert "catálogo incompleto" not in dialog.result_label.text()


class TestDuplicadosDeCalidadEnLaTabla:
    """Las variantes descartadas se ven, pero la casilla dice la verdad."""

    def _dos_variantes(self, fake_api):
        fake_api.served_channels = [
            dict(_row(name="LA 1 FULL HD", url="http://x/fhd",
                      tvg_id="La 1 HD"), served=True, duplicate=False),
            dict(_row(name="LA 1 HD", url="http://x/hd", tvg_id="La 1 HD"),
                 served=False, duplicate=True),
        ]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD"}]

    def test_la_variante_descartada_sale_desmarcada(self, dialog, fake_api):
        self._dos_variantes(fake_api)
        dialog._refresh_mapping()
        w = dialog.epg_mapping
        assert (w.table.item(0, w.COL_ACTIVE).checkState()
                == Qt.CheckState.Checked)
        assert (w.table.item(1, w.COL_ACTIVE).checkState()
                == Qt.CheckState.Unchecked)

    def test_la_fila_duplicada_explica_por_que_no_se_sirve(self, dialog,
                                                           fake_api):
        self._dos_variantes(fake_api)
        dialog._refresh_mapping()
        item = dialog.epg_mapping.table.item(1, dialog.epg_mapping.COL_NAME)
        assert "prioridad de calidad" in item.toolTip()
        # la que se sirve no lleva el aviso
        assert dialog.epg_mapping.table.item(
            0, dialog.epg_mapping.COL_NAME).toolTip() == ""

    def test_el_resumen_cuenta_las_variantes_descartadas(self, dialog,
                                                         fake_api):
        self._dos_variantes(fake_api)
        dialog._refresh_mapping()
        assert "variantes descartadas por calidad" in dialog.result_label.text()


class TestFiltrarCanalDesdeElMenu:
    """«Filtrar canal»: el canal elegido acaba en las reglas como inclusión."""

    def _con_dos(self, dialog, fake_api):
        fake_api.served_channels = [
            _row(name="LA 1 HD", url="http://x/la1.ts", tvg_id="La 1 HD"),
            _row(name="LA 2 HD", url="http://x/la2.ts", tvg_id="La 2"),
        ]
        fake_api.epg = [{"id": "La 1 HD", "display_name": "La 1 HD"},
                        {"id": "La 2", "display_name": "La 2"}]
        dialog._refresh_mapping()

    def test_el_menu_ofrece_filtrar_canal(self, dialog, fake_api):
        self._con_dos(dialog, fake_api)
        textos = [a.text() for a in dialog.epg_mapping.row_menu([0]).actions()
                  if a.text()]
        assert "Filtrar canal" in textos

    def test_un_canal_se_excluye_con_and(self, dialog, fake_api):
        self._con_dos(dialog, fake_api)
        menu = dialog.epg_mapping.row_menu([0])
        next(a for a in menu.actions()
             if a.text() == "Filtrar canal").trigger()
        reglas = dialog.rules_widget.get_rules()
        assert len(reglas) == 1
        regla = reglas[0]
        assert regla.field == "custom-filter"
        assert regla.pattern == "!{LA 1 HD}"      # exclusión
        assert regla.exclude is True
        assert regla.operator == "and"

    def test_varios_canales_se_excluyen_todos_con_and(self, dialog, fake_api):
        self._con_dos(dialog, fake_api)
        menu = dialog.epg_mapping.row_menu([0, 1])
        next(a for a in menu.actions()
             if a.text() == "Filtrar canales").trigger()
        reglas = dialog.rules_widget.get_rules()
        assert [(r.pattern, r.operator) for r in reglas] == [
            ("!{LA 1 HD}", "and"), ("!{LA 2 HD}", "and")]
        # se serializan como en el fichero
        assert [r.serialize() for r in reglas] == [
            "custom-filter:!{LA 1 HD}:0:and",
            "custom-filter:!{LA 2 HD}:0:and"]

    def test_no_salta_de_pestaña_y_aplica_al_momento(self, dialog, fake_api,
                                                     wait_save):
        """Sin cambiar de pestaña: se manda el PUT con la exclusión."""
        self._con_dos(dialog, fake_api)
        antes = dialog.tabs.currentIndex()
        dialog.epg_mapping.row_menu([0]).actions()[-1].trigger()
        wait_save(dialog)
        assert dialog.tabs.currentIndex() == antes
        puts = {c[1]: c[2] for c in fake_api.calls if c[0] == "put"}
        enviado = puts["/targets/0/rules"]
        assert enviado["reload"] is True
        assert "custom-filter:!{LA 1 HD}:0:and" in enviado["rules"]

    def test_clics_seguidos_no_encolan_recargas_en_cascada(self, dialog,
                                                           fake_api,
                                                           wait_save,
                                                           monkeypatch):
        """Un clic mientras hay trabajo en curso no lanza otro: queda pendiente
        y al terminar se aplica UNA vez con las reglas definitivas."""
        import time as _time

        self._con_dos(dialog, fake_api)
        original = fake_api.put

        def put_lento(path, payload, timeout=None):
            _time.sleep(0.3)      # simula el pipeline del demonio
            return original(path, payload, timeout=timeout)

        monkeypatch.setattr(fake_api, "put", put_lento)
        dialog.epg_mapping.row_menu([0]).actions()[-1].trigger()
        assert dialog._saving is True          # el primer trabajo está en vuelo
        dialog.epg_mapping.row_menu([1]).actions()[-1].trigger()
        assert dialog._pending_apply is True
        wait_save(dialog)
        puts = [c[2] for c in fake_api.calls
                if c[0] == "put" and c[1] == "/targets/0/rules"]
        assert len(puts) == 2, puts            # el primero + uno con las dos
        ambas = [" ".join(p["rules"]) for p in puts]
        assert any("!{LA 1 HD}" in texto and "!{LA 2 HD}" in texto
                   for texto in ambas), puts

    def test_el_repintado_ya_no_pide_preview(self, dialog, fake_api,
                                              monkeypatch):
        llamadas = []
        monkeypatch.setattr(dialog, "_refresh_filter_preview",
                            lambda: llamadas.append(1))
        dialog._refresh_mapping()
        assert llamadas == []

    def test_entrar_en_filtros_refresca_el_preview(self, dialog, monkeypatch):
        """Al entrar en Filtros se programa el preview (con su debounce)."""
        llamadas = []
        monkeypatch.setattr(dialog, "_schedule_filter_preview",
                            lambda: llamadas.append(1))
        dialog._on_tab_changed(dialog.tabs.indexOf(dialog.tab_filters))
        assert llamadas == [1]
        # otras pestañas no lo piden
        dialog._on_tab_changed(dialog.tabs.indexOf(dialog.tab_config))
        assert llamadas == [1]

    def test_los_nombres_repetidos_se_añaden_una_vez(self, dialog, fake_api):
        self._con_dos(dialog, fake_api)
        dialog._filter_channels(["LA 1 HD", "LA 1 HD"])
        assert len(dialog.rules_widget.get_rules()) == 1


class TestAplicarFiltros:
    """«Aplicar» de Filtros iptv guarda las reglas y reejecuta el pipeline."""

    def test_manda_las_reglas_con_recarga(self, dialog, fake_api, wait_save):
        from src.application.services.list_manager.models import FilterRule

        dialog.rules_widget.add_rules([FilterRule(
            field="custom-filter", pattern="7TV MALAGA HD", exclude=True,
            operator="and")])
        dialog._apply_rules_now()
        wait_save(dialog)
        puts = {c[1]: c[2] for c in fake_api.calls if c[0] == "put"}
        enviado = puts["/targets/0/rules"]
        assert enviado["reload"] is True
        assert "custom-filter:!{7TV MALAGA HD}:0:and" in enviado["rules"]
        assert "Filtros aplicados" in dialog.result_label.text()
        # la petición reejecuta el pipeline: no puede cortar al timeout corto
        from src.infrastructure.client.api_client import PIPELINE_TIMEOUT

        assert fake_api.last_put_timeout == PIPELINE_TIMEOUT

    def test_guardar_y_aplicar_no_se_pisan(self, dialog, fake_api, wait_save):
        """Un «Aplicar» durante un guardado se ignora (un lote a la vez)."""
        dialog._saving = True
        dialog._apply_rules_now()
        assert not [c for c in fake_api.calls if c[0] == "put"]
        dialog._saving = False


class TestFiltroEncolado:
    """Un «Filtrar canal» durante un guardado se aplica al terminar."""

    def test_encola_y_aplica_despues(self, dialog, fake_api, wait_save):
        dialog._saving = True
        dialog._filter_channels(["AndaluciaTV"])
        assert "se aplicará" in dialog.result_label.text()
        assert not [c for c in fake_api.calls if c[0] == "put"]

        dialog._saving = False
        dialog._save_pending = {"name": "x", "port": 8080,
                                "rules_objects": dialog.rules_widget.get_rules(),
                                "quality_priority": {}, "overrides": {},
                                "disabled": []}
        dialog._on_save_finished({"ok": True, "served": 3})
        wait_save(dialog)
        puts = [c[2] for c in fake_api.calls if c[0] == "put"]
        assert any(p.get("reload") is True for p in puts)


class TestActualizarGuias:
    """El botón del mapeo descarga guías explícitamente."""

    def test_el_boton_llama_a_la_api(self, dialog, fake_api, wait_save):
        assert dialog.epg_mapping.refresh_guides_btn.text() == "&Actualizar guías"
        dialog.epg_mapping.refresh_guides_btn.click()
        wait_save(dialog)
        assert ("refresh_epg", "0") in fake_api.calls
        assert "Guías actualizadas" in dialog.result_label.text()

    def test_si_hay_guardado_en_curso_no_se_lanza(self, dialog, fake_api):
        dialog._saving = True
        dialog.epg_mapping.refresh_guides_btn.click()
        assert not [c for c in fake_api.calls if c[0] == "refresh_epg"]
        assert "guardado en curso" in dialog.result_label.text()


class TestLaVistaSobreviveAlRefresco:
    """Repintar no devuelve al usuario al principio ni le quita la selección.

    El refresco lo dispara el demonio (revisión nueva) cada pocos segundos; si
    la tabla saltara al principio y perdiera la selección, mapear 800 canales
    sería imposible.
    """

    def _canales(self, total: int):
        return [_row(name=f"Canal {i:02d}", url=f"http://x/{i}.ts",
                     tvg_id=f"Canal {i:02d}") for i in range(total)]

    def test_conserva_la_seleccion_por_identidad(self, dialog, fake_api):
        fake_api.served_channels = self._canales(20)
        dialog._refresh_mapping()
        tabla = dialog.epg_mapping.table
        w = dialog.epg_mapping
        ultima = tabla.columnCount() - 1
        tabla.setRangeSelected(QTableWidgetSelectionRange(5, 0, 5, ultima), True)
        tabla.setRangeSelected(QTableWidgetSelectionRange(9, 0, 9, ultima), True)

        # el orden cambia (fila nueva delante): las mismas filas siguen elegidas
        canales = self._canales(20)
        canales.insert(0, _row(name="Nuevo", url="http://x/nuevo.ts"))
        fake_api.served_channels = canales
        dialog._refresh_mapping()

        elegidas = {tabla.item(r, w.COL_NAME).text() for r in w.selected_rows()}
        assert elegidas == {"Canal 05", "Canal 09"}

    def test_conserva_la_fila_superior_visible(self, dialog, fake_api):
        fake_api.served_channels = self._canales(60)
        dialog._refresh_mapping()
        tabla = dialog.epg_mapping.table
        w = dialog.epg_mapping
        tabla.resize(1000, 400)
        tabla.scrollToItem(tabla.item(25, w.COL_NAME),
                           QAbstractItemView.ScrollHint.PositionAtTop)
        arriba = tabla.rowAt(0)

        # el demonio devuelve lo mismo con dos filas nuevas al principio
        canales = self._canales(60)
        canales[:0] = [_row(name="Nueva A", url="http://x/na.ts"),
                       _row(name="Nueva B", url="http://x/nb.ts")]
        fake_api.served_channels = canales
        dialog._refresh_mapping()

        assert tabla.rowAt(0) == arriba + 2, (
            "la fila superior tiene que seguir siendo el mismo canal")


class TestPaginacionDeLaTabla:
    """La tabla pide una página; el total se conoce sin bajar el catálogo entero."""

    def _canales(self, total: int):
        return [_row(name=f"Canal {i:02d}", url=f"http://x/{i}.ts",
                     tvg_id=f"Canal {i:02d}") for i in range(total)]

    def test_pide_una_pagina_y_no_el_catalogo_entero(self, dialog, fake_api):
        fake_api.served_channels = self._canales(300)
        dialog._refresh_mapping()
        assert fake_api.last_limit == MAPPING_PAGE
        assert dialog.epg_mapping.table.rowCount() == MAPPING_PAGE

    def test_el_boton_dice_cuantas_hay_y_trae_la_siguiente(
            self, dialog, fake_api):
        fake_api.served_channels = self._canales(300)
        dialog._refresh_mapping()
        boton = dialog.epg_mapping.more_btn
        assert not boton.isHidden()
        assert "50 de 300" in boton.text()

        boton.click()
        assert fake_api.last_limit == 2 * MAPPING_PAGE
        assert dialog.epg_mapping.table.rowCount() == 2 * MAPPING_PAGE

    def test_sin_filas_pendientes_el_boton_no_aparece(self, dialog, fake_api):
        fake_api.served_channels = self._canales(3)
        dialog._refresh_mapping()
        assert dialog.epg_mapping.more_btn.isHidden()

    def test_un_apagado_fuera_de_la_pagina_no_se_pierde(self, dialog, fake_api):
        """Guardar no puede reactivar lo que no está en la página cargada."""
        fake_api.served_channels = self._canales(300)
        dialog._refresh_mapping()
        w = dialog.epg_mapping
        w._manual_off.add("http://x/250.ts")   # apagado que no está cargado
        assert "http://x/250.ts" in w.get_disabled()


class TestDecisionesEnFichas:
    """El fichero guarda las decisiones por identidad; el demonio las publica
    plegadas a url. La UI tiene que partir de esa lista completa, no de la
    página que está viendo, o al guardar borraría lo que no ve."""

    def _canales(self, total):
        return [_row(name=f"Canal {i:02d}", url=f"http://x/{i}.ts",
                     tvg_id=f"Canal {i:02d}") for i in range(total)]

    def test_un_apagado_fuera_de_la_pagina_no_se_pierde(self, dialog, fake_api):
        fake_api.served_channels = self._canales(80)
        fake_api.target_definition = {"epg_overrides": {},
                                      "disabled_channels": ["http://x/70.ts"]}
        dialog._refresh_mapping()          # página de 50: la 70 no se carga
        assert "http://x/70.ts" in dialog.epg_mapping.get_disabled()

    def test_un_mapeo_del_demonio_se_ve_en_el_desplegable(self, dialog, fake_api):
        fake_api.served_channels = [_row(name="LA 1", url="http://x/la1.ts")]
        fake_api.epg = [{"id": "M+ LALIGA", "display_name": "M+ LALIGA"}]
        fake_api.target_definition = {
            "epg_overrides": {"http://x/la1.ts": "M+ LALIGA"},
            "disabled_channels": []}
        dialog._refresh_mapping()
        combo = dialog.epg_mapping.table.cellWidget(
            0, dialog.epg_mapping.COL_EPG)
        assert combo.current_value().startswith("M+ LALIGA")
        assert dialog.epg_mapping.get_overrides() == {
            "http://x/la1.ts": "M+ LALIGA"}
