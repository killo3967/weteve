"""UI del gestor de listas: wizard 3 pasos + control del servicio web.

Especificación: odd/tasks/list-manager.md. Los diálogos son delgados: delegan
en el pipeline (application) y en el servidor (infrastructure/web).
"""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QTabWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.application.services.list_manager.epg_matcher import EpgMatcher
from src.application.services.list_manager.models import (
    FilterField,
    FilterRule,
    PipelineTarget,
)
from src.application.services.list_manager.source_catalog import SourceCatalog
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.infrastructure.web.playlist_server import (
    PlaylistServer,
    find_port_owner,
    port_in_use,
)

logger = logging.getLogger(__name__)

FILTER_FIELDS: list[tuple[FilterField, str]] = [
    ("group-title", "Grupo"),
    ("custom-filter", "Nombre"),
]


class FilterRulesWidget(QWidget):
    """Tabla editable de reglas de filtro simple (modelo xTeVe interno)."""

    def __init__(self, parent=None, on_apply=None, dialog=None):
        super().__init__(parent)
        self._dialog = dialog  # proveedor de grupos (ListManagerDialog)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Operador", "Campo", "Texto", "Acción", "Mayús./minús.", ""])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        add_btn = QPushButton("&Añadir regla")
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton("&Quitar")
        remove_btn.clicked.connect(self._remove_row)
        self.groups_btn = QPushButton("&Grupos…")
        self.groups_btn.setToolTip(
            "Elegir un grupo real de la fuente y ponerlo en la regla seleccionada")
        self.groups_btn.clicked.connect(self._pick_group)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addWidget(self.groups_btn)
        buttons.addStretch()
        if on_apply is not None:
            apply_btn = QPushButton("&Aplicar")
            apply_btn.clicked.connect(on_apply)
            buttons.addWidget(apply_btn)
        layout.addLayout(buttons)

    # Valores por defecto al crear una fila nueva: "AND + Excluir" es lo más
    # habitual (afinar una lista existente quitando canales).
    DEFAULT_OPERATOR = "and"
    DEFAULT_EXCLUDE = True

    def _add_row(self, _checked: bool = False, field: FilterField = "custom-filter",
                 text: str = "", exclude: bool | None = None,
                 case_sensitive: bool = False,
                 operator: str | None = None) -> None:
        if exclude is None:
            exclude = self.DEFAULT_EXCLUDE
        if operator is None:
            operator = self.DEFAULT_OPERATOR
        row = self.table.rowCount()
        self.table.insertRow(row)

        op_combo = QComboBox()
        op_combo.addItem("OR", "or")
        op_combo.addItem("AND", "and")
        op_combo.setCurrentIndex(1 if operator == "and" else 0)
        self.table.setCellWidget(row, 0, op_combo)

        field_combo = QComboBox()
        self._fill_field_combo(field_combo)
        self._select_field(field_combo, field)
        field_combo.currentIndexChanged.connect(
            lambda _i, r=row: self._on_field_changed(r))
        self.table.setCellWidget(row, 1, field_combo)

        self._set_text_cell(row, field, text)

        action_combo = QComboBox()
        action_combo.addItem("Incluir", False)
        action_combo.addItem("Excluir", True)
        action_combo.setCurrentIndex(1 if exclude else 0)
        self.table.setCellWidget(row, 3, action_combo)

        cs_check = QCheckBox("Respetar mayús./minús.")
        cs_check.setChecked(case_sensitive)
        self.table.setCellWidget(row, 4, cs_check)

        self.table.setCellWidget(row, 5, self._build_arrows_widget(row))

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    # -- campo dinámico: lo que traiga la fuente ------------------------------

    def _group_fields(self) -> dict[str, list[str]]:
        """Campos de agrupación disponibles vía el diálogo, o solo group-title."""
        if self._dialog is None:
            return {}
        try:
            return dict(self._dialog.available_group_fields())
        except Exception:  # noqa: BLE001 - fuente no disponible aún
            return {}

    def _fill_field_combo(self, combo: QComboBox) -> None:
        """Rellena el combo Campo: Nombre fijo + un opción por campo de grupo."""
        combo.clear()
        combo.addItem("Nombre", "custom-filter")
        labels = {"group-title": "Grupo", "tvg-group": "Grupo (tvg-group)",
                  "extgrp": "Grupo (#EXTGRP)", "source": "Lista"}
        for fname in self._group_fields():
            combo.addItem(labels.get(fname, fname), fname)
        # fallback si la fuente aún no tiene grupos
        if combo.count() == 1:
            combo.addItem("Grupo", "group-title")

    def refresh_group_options(self) -> None:
        """Repuebla campos y valores de grupo tras resolver la fuente.

        Las reglas se construyen en el __init__ antes de cargar el M3U, así que
        sus desplegables nacen vacíos; sin esto, seleccionar "Grupo" no muestra
        ningún grupo hasta cambiar el campo a mano.
        """
        for row in range(self.table.rowCount()):
            combo_campo = self.table.cellWidget(row, 1)
            if combo_campo is None:
                continue
            field = combo_campo.currentData()
            text = self._cell_text(row)
            combo_campo.blockSignals(True)
            self._fill_field_combo(combo_campo)
            self._select_field(combo_campo, field)
            combo_campo.blockSignals(False)
            self._set_text_cell(row, field, text)

    @staticmethod
    def _select_field(combo: QComboBox, field: FilterField) -> None:
        idx = combo.findData(field)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # -- flechas de reordenación ----------------------------------------------

    def _build_arrows_widget(self, row: int) -> QWidget:
        w = QWidget(self.table)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        up = QPushButton("↑")
        up.setFixedWidth(24)
        up.clicked.connect(lambda _c=False, r=row: self._move_row(r, -1))
        down = QPushButton("↓")
        down.setFixedWidth(24)
        down.clicked.connect(lambda _c=False, r=row: self._move_row(r, +1))
        lay.addWidget(up)
        lay.addWidget(down)
        return w

    def _move_row(self, row: int, delta: int) -> None:
        """Mueve una fila arriba (-1) o abajo (+1).

        Reconstruye la tabla desde el estado capturado: mover widgets de
        sitio con removeCellWidget/setCellWidget crucea la propiedad de los
        widgets en Qt y provocaba cierres inesperados al pulsar las flechas.
        """
        n = self.table.rowCount()
        if not (0 <= row < n):
            return
        target = row + delta
        if not (0 <= target < n):
            return
        if target == row:
            return

        rows = [self._row_state(r) for r in range(n)]
        rows[row], rows[target] = rows[target], rows[row]

        current = self.table.currentRow()
        self.table.setRowCount(0)
        for state in rows:
            self._add_row(
                field=state["field"], text=state["text"],
                exclude=state["exclude"], case_sensitive=state["case"],
                operator=state["operator"],
            )
        self.table.setCurrentCell(target, 2)

    def _row_state(self, row: int) -> dict:
        """Captura el estado completo de una fila para reconstruirla."""
        op_combo = self.table.cellWidget(row, 0)
        field_combo = self.table.cellWidget(row, 1)
        action_combo = self.table.cellWidget(row, 3)
        cs_check = self.table.cellWidget(row, 4)
        return {
            "operator": op_combo.currentData() if op_combo else "or",
            "field": field_combo.currentData() if field_combo else "custom-filter",
            "text": self._cell_text(row),
            "exclude": action_combo.currentData() if action_combo else False,
            "case": cs_check.isChecked() if cs_check else False,
        }

    # -- celda de texto: combo de grupos o texto libre ------------------------

    def _set_text_cell(self, row: int, field: FilterField, text: str) -> None:
        """Campo de grupo → combo editable con sus valores; Nombre → texto."""
        if field == "custom-filter":
            self.table.removeCellWidget(row, 2)
            self.table.setItem(row, 2, QTableWidgetItem(text))
            return
        combo = QComboBox()
        combo.setEditable(True)
        if field == "group-title":
            groups = self._current_groups()
        else:
            groups = self._group_fields().get(field, [])
        combo.addItems(groups)
        if text:
            combo.setCurrentText(text)
        self.table.setCellWidget(row, 2, combo)

    def _on_field_changed(self, row: int) -> None:
        """Al cambiar el campo, convertir la celda de texto conservando el valor."""
        combo_campo = self.table.cellWidget(row, 1)
        if combo_campo is None:
            return
        field = combo_campo.currentData()
        current = self._cell_text(row)
        self._set_text_cell(row, field, current)

    def _cell_text(self, row: int) -> str:
        cell = self.table.cellWidget(row, 2)
        if isinstance(cell, QComboBox):
            return cell.currentText()
        item = self.table.item(row, 2)
        return item.text() if item else ""

    # -- selección de grupo -------------------------------------------------

    def _current_groups(self) -> list[str]:
        """Grupos disponibles vía el diálogo, o lista vacía."""
        if self._dialog is None:
            return []
        try:
            return list(self._dialog.available_groups())
        except Exception:  # noqa: BLE001 - fuente no disponible aún
            return []

    def _pick_group(self) -> None:
        """Muestra el menú de grupos y lo aplica a la fila seleccionada."""
        row = self.table.currentRow()
        if row < 0:
            row = 0 if self.table.rowCount() else -1
        if row < 0:
            self._add_row(exclude=False, operator="or")
            row = 0
        self._pick_group_for_row(row)

    def _pick_group_for_row(self, row: int) -> None:
        """Muestra el menú de grupos y aplica el elegido a la fila."""
        groups = self._current_groups()
        if not groups:
            self._warn_no_groups()
            return
        chosen = self._choose_group(groups)
        if chosen is None:
            return
        self._apply_group_to_row(row, chosen)

    def _choose_group(self, groups: list[str]) -> str | None:
        """Interacción: menú desplegable. None = cancelado.

        Separada para poder sustituirla en tests (evita el exec modal).
        """
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        for g in groups:
            menu.addAction(g)
        action = menu.exec(self.groups_btn.mapToGlobal(
            self.groups_btn.rect().bottomLeft()))
        return action.text() if action is not None else None

    def _warn_no_groups(self) -> None:
        """Aviso cuando la fuente aún no está cargada."""
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.information(
            self, "Sin grupos",
            "No hay grupos disponibles. Aplica primero la fuente "
            "(botón Aplicar del diálogo) para cargar los canales.")

    def _apply_group_to_row(self, row: int, group: str) -> None:
        """Pone el grupo en la celda de texto y cambia el campo a "Grupo"."""
        self._set_text_cell(row, "group-title", group)
        combo = self.table.cellWidget(row, 1)
        if combo is not None:
            self._select_field(combo, "group-title")

    def load_rules(self, rules: list[FilterRule]) -> None:
        self.table.setRowCount(0)
        for rule in rules:
            # Decodifica la sintaxis xTeVe ({inc} / !{exc}) a filas simples.
            exclude = False
            pattern = rule.pattern
            import re

            m = re.search(r"!+\{[^.]+\}", pattern)
            if m:
                exclude = True
                text = m.group(0)[2:-1]
            else:
                m = re.search(r"\{[^.]+\}", pattern)
                text = m.group(0)[1:-1] if m else pattern
            self._add_row(
                field=rule.field, text=text, exclude=exclude,
                case_sensitive=rule.case_sensitive, operator=rule.operator,
            )

    def get_rules(self) -> list[FilterRule]:
        rules: list[FilterRule] = []
        for row in range(self.table.rowCount()):
            op_combo = self.table.cellWidget(row, 0)
            field_combo = self.table.cellWidget(row, 1)
            action_combo = self.table.cellWidget(row, 3)
            cs_check = self.table.cellWidget(row, 4)
            text = self._cell_text(row).strip()
            if not text:
                continue
            field: FilterField = field_combo.currentData()
            exclude: bool = action_combo.currentData()
            pattern = f"!{{{text}}}" if exclude else text
            rules.append(FilterRule(
                field=field, pattern=pattern, exclude=exclude,
                case_sensitive=cs_check.isChecked(),
                operator=op_combo.currentData(),
            ))
        return rules


class EpgMappingWidget(QWidget):
    """Editor manual de asociación EPG con sugerencias."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel(
            "Canales sin guía EPG. Edita la columna 'EPG asignado' "
            "(puedes copiar un id de las sugerencias):"
        )
        layout.addWidget(self.info_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Canal M3U", "(url)", "EPG asignado", "Sugerencias"]
        )
        self.table.setColumnHidden(1, True)  # url como clave estable, oculta
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        self._matcher: EpgMatcher | None = None
        self._epg_names: dict[str, str] = {}
        self._updating = False
        # Sugerencias perezosas: se calculan al posicionar/editar cada fila.
        self.table.currentCellChanged.connect(self._on_current_cell_changed)

    def _on_current_cell_changed(self, row: int, _col: int, _prow: int, _pcol: int) -> None:
        if row < 0:
            return
        sug_item = self.table.item(row, 3)
        if sug_item is not None and not sug_item.text():
            self._fill_suggestions(row)

    def set_epg(self, epg) -> None:
        self._matcher = EpgMatcher(epg) if epg is not None else None
        self._epg_names = dict(epg.channel_names) if epg is not None else {}

    def load_unmatched(self, channels, overrides: dict[str, str]) -> None:
        """channels: lista de MappedChannel sin match (matched_epg_id is None).

        La tabla se llena en bloque y las sugerencias se calculan perezosamente:
        difflib contra todo el EPG por canal es O(n*m) y con listas grandes
        (decenas de miles de canales) bloquea la UI varios minutos.
        """
        self._updating = True
        self.table.setRowCount(channels.__len__())
        for row, ch in enumerate(channels):
            name_item = QTableWidgetItem(ch.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name_item)

            url_item = QTableWidgetItem(ch.url)
            self.table.setItem(row, 1, url_item)

            current = overrides.get(ch.url, "")
            assigned = QTableWidgetItem(self._epg_names.get(current, current))
            assigned.setData(Qt.ItemDataRole.UserRole, current)
            self.table.setItem(row, 2, assigned)

            # Sugerencias diferidas: se rellenan al mostrar la fila.
            self.table.setItem(row, 3, QTableWidgetItem(""))
        self._updating = False

    def _suggestions_text(self, name: str, current: str) -> str:
        if self._matcher is None:
            return ""
        parts = []
        for cid, display in self._matcher.suggestions(name, limit=5):
            mark = " ←" if cid == current else ""
            parts.append(f"{display}{mark}")
        return "; ".join(parts)

    def _fill_suggestions(self, row: int) -> None:
        """Rellena la columna de sugerencias de una fila (bajo demanda)."""
        name_item = self.table.item(row, 0)
        if name_item is None:
            return
        current_item = self.table.item(row, 2)
        current = current_item.data(Qt.ItemDataRole.UserRole) if current_item else ""
        self.table.item(row, 3).setText(
            self._suggestions_text(name_item.text(), current or "")
        )

    def _suggestions_text(self, name: str, current: str) -> str:
        if self._matcher is None:
            return ""
        parts = []
        for cid, display in self._matcher.suggestions(name, limit=5):
            mark = " ←" if cid == current else ""
            parts.append(f"{display}{mark}")
        return "; ".join(parts)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 2:
            return
        text = item.text().strip()
        # Resolución: nombre mostrado -> channel-id del EPG
        cid = text
        for channel_id, display in self._epg_names.items():
            if display == text:
                cid = channel_id
                break
        item.setData(Qt.ItemDataRole.UserRole, cid)

    def get_overrides(self) -> dict[str, str]:
        """Devuelve url -> channel-id asignado manualmente (solo los con valor)."""
        overrides: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            url_item = self.table.item(row, 1)
            assigned = self.table.item(row, 2)
            if url_item is None or assigned is None:
                continue
            cid = assigned.data(Qt.ItemDataRole.UserRole)
            if cid:
                overrides[url_item.text()] = cid
        return overrides


class ListManagerDialog(QDialog):
    """Diálogo principal del gestor: wizard 3 pasos + servicio web."""

    def __init__(self, parent, config: dict, sources: dict | None = None,
                 pipeline: ListManagerPipeline | None = None):
        super().__init__(parent)
        self.setWindowTitle("weteve — Gestor de listas")
        # weteve es standalone: sin listas de una app anfitriona. El parámetro
        # ``sources`` se mantiene por compatibilidad pero queda vacío.
        self._sources = sources or {}  # {idx: {name, m3u, filter, epg}}
        self._config = config
        self._pipeline = pipeline or ListManagerPipeline()
        self._server = PlaylistServer(
            host=str(config.get("bind", "127.0.0.1")),
            port=int(config.get("port", 8080)),
        )

        layout = QVBoxLayout(self)

        # Pestañas: cada sección ocupa todo el alto disponible.
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # Orígenes (fuente) — pestaña legacy oculta: los accesores
        # (custom_m3u_edit, source_combo…) siguen vivos para compatibilidad,
        # pero la edición de fuentes se hace en las pestañas de cartera.
        self.tab_sources = QWidget(self)
        source_layout = QVBoxLayout(self.tab_sources)
        source_group = QGroupBox("Orígenes")
        form = QFormLayout(source_group)
        self.name_edit = QLineEdit(str(config.get("name", "")))
        form.addRow("&Nombre del resultado:", self.name_edit)
        self.source_mode_combo = QComboBox()
        self.source_mode_combo.addItem("Origen propio (URL o fichero)", "custom")
        form.addRow("&Tipo de origen:", self.source_mode_combo)
        self.source_combo = QComboBox()
        for idx in sorted(self._sources):
            self.source_combo.addItem(str(self._sources[idx].get("name", f"Fuente {idx}")), idx)
        saved_source = config.get("source")
        if saved_source is not None:
            pos = self.source_combo.findData(int(saved_source))
            if pos >= 0:
                self.source_combo.setCurrentIndex(pos)
        form.addRow("&Lista M3U:", self.source_combo)

        custom_row = QHBoxLayout()
        self.custom_m3u_edit = QLineEdit(str(config.get("custom_m3u", "")))
        self.custom_m3u_edit.setPlaceholderText("https://…/lista.m3u  o  C:\\ruta\\fichero.m3u")
        browse_m3u = QPushButton("&Examinar…")
        browse_m3u.clicked.connect(self._browse_m3u)
        custom_row.addWidget(self.custom_m3u_edit)
        custom_row.addWidget(browse_m3u)
        custom_m3u_container = QWidget()
        custom_m3u_container.setLayout(custom_row)
        form.addRow("M3U propio:", custom_m3u_container)

        self.epg_combo = QComboBox()
        self.epg_combo.addItem("(sin EPG)", -1)
        for idx in sorted(self._sources):
            if self._sources[idx].get("epg"):
                self.epg_combo.addItem(
                    f"{self._sources[idx].get('name', idx)} — EPG", idx
                )
        saved_epg = config.get("epg_source")
        if saved_epg is not None:
            pos = self.epg_combo.findData(int(saved_epg))
            if pos >= 0:
                self.epg_combo.setCurrentIndex(pos)
        form.addRow("&Guía EPG:", self.epg_combo)

        custom_epg_row = QHBoxLayout()
        self.custom_epg_edit = QLineEdit(str(config.get("custom_epg", "")))
        self.custom_epg_edit.setPlaceholderText("https://…/guia.xml  o  C:\\ruta\\guia.xml (opcional)")
        browse_epg = QPushButton("E&xaminar…")
        browse_epg.clicked.connect(self._browse_epg)
        custom_epg_row.addWidget(self.custom_epg_edit)
        custom_epg_row.addWidget(browse_epg)
        custom_epg_container = QWidget()
        custom_epg_container.setLayout(custom_epg_row)
        form.addRow("EPG propio:", custom_epg_container)

        self.source_mode_combo.currentIndexChanged.connect(self._update_source_mode)
        # Al escribir o elegir una ruta se recargan los grupos de las reglas.
        self.custom_m3u_edit.editingFinished.connect(self._update_source_mode)
        self.source_combo.currentIndexChanged.connect(self._refresh_mapping)
        self.epg_combo.currentIndexChanged.connect(self._refresh_mapping)
        source_layout.addWidget(source_group)
        # Cartera de fuentes: dos pestañas con la misma forma; "Activa"
        # marca qué filas aporta este target a la unión.
        from src.infrastructure.ui.source_catalog_widget import SourceCatalogWidget
        from src.infrastructure.client.sources_store import (
            load_catalog, migrate_legacy_sources,
        )
        config_path = config.get("_config_path")
        catalog = SourceCatalog()
        if config_path:
            try:
                catalog = migrate_legacy_sources(Path(config_path))
            except Exception:  # noqa: BLE001 - cartera ilegible = vacía
                logger.warning("Cartera ilegible en %s", config_path, exc_info=True)
        self.catalog_m3u_widget = SourceCatalogWidget(kind="m3u", catalog=catalog)
        self.catalog_epg_widget = SourceCatalogWidget(kind="epg", catalog=catalog)
        for widget, refs in (
            (self.catalog_m3u_widget, config.get("m3u_refs", [])),
            (self.catalog_epg_widget, config.get("epg_refs", [])),
        ):
            widget.set_selection([r.id for r in refs])
        self.tabs.addTab(self.catalog_m3u_widget, "Listas IPTV")
        self.tabs.addTab(self.catalog_epg_widget, "Listas EPG")
        # la pestaña legacy ya no va en el QTabWidget: sin contenedor se
        # quedaría flotando sobre la pestaña activa (texto superpuesto)
        self.tab_sources.hide()

        # Filtro de canales
        self.tab_filters = QWidget(self)
        filter_layout = QVBoxLayout(self.tab_filters)
        self.rules_widget = FilterRulesWidget(on_apply=self._refresh_mapping,
                                              dialog=self)
        self.rules_widget.load_rules(config.get("rules", []))
        filter_layout.addWidget(self.rules_widget)
        self.tabs.addTab(self.tab_filters, "Filtros")

        # Mapeado canal<-->epg
        self.tab_mapping = QWidget(self)
        epg_layout = QVBoxLayout(self.tab_mapping)
        self.epg_mapping = EpgMappingWidget()
        epg_layout.addWidget(self.epg_mapping)
        self.result_label = QLabel("")
        epg_layout.addWidget(self.result_label)
        self.tabs.addTab(self.tab_mapping, "Mapeado EPG")

        # Servicio web
        service_group = QGroupBox("Servicio web")
        service_form = QFormLayout(service_group)
        port_row = QHBoxLayout()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(config.get("port", 8080)))
        port_row.addWidget(QLabel("Puerto:"))
        port_row.addWidget(self.port_spin)
        self.bind_combo = QComboBox()
        self.bind_combo.addItem("Solo este equipo (127.0.0.1)", "127.0.0.1")
        self.bind_combo.addItem("Toda la red local (0.0.0.0)", "0.0.0.0")
        saved_bind = str(config.get("bind", "127.0.0.1"))
        pos = self.bind_combo.findData(saved_bind)
        self.bind_combo.setCurrentIndex(max(pos, 0))
        port_row.addWidget(QLabel("Acceso:"))
        port_row.addWidget(self.bind_combo)
        self.service_button = QPushButton("&Iniciar servicio")
        self.service_button.clicked.connect(self._toggle_service)
        port_row.addWidget(self.service_button)
        port_row.addStretch()
        service_form.addRow(port_row)
        self.port_status_label = QLabel("")
        service_form.addRow(self.port_status_label)
        self.port_spin.valueChanged.connect(self._check_port)
        self.bind_combo.currentIndexChanged.connect(self._check_port)
        self._check_port()
        layout.addWidget(service_group)

        # Demonio: estado y aplicación de la config al servicio permanente
        from src.application.services.daemon.target_store import TargetStore
        from src.infrastructure.ui.daemon_panel import DaemonPanel
        config_path = config.get("_config_path")
        store = TargetStore(config_path) if config_path else None
        self.daemon_panel = DaemonPanel(
            target_store=store, target_id=str(config.get("target_id", "0")),
            get_target_config=self._build_target, config_path=config_path)
        self.daemon_panel.refresh_status()
        layout.addWidget(self.daemon_panel)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("&Guardar")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # weteve es standalone: el único modo de origen es "custom" (URL o
        # fichero propio). Esta llamada carga la fuente por primera vez y
        # alimenta los desplegables de grupos de las reglas.
        self._update_source_mode()

    def _on_save(self) -> None:
        """Guardar persiste la config SIN cerrar el diálogo."""
        from src.infrastructure.client.persistencia import save_config
        from src.infrastructure.client.sources_store import save_catalog

        config_path = self._config.get("_config_path")
        if config_path is None:
            return
        # 1) cartera de fuentes (ambas pestañas)
        catalog = SourceCatalog([
            *self.catalog_m3u_widget.get_catalog().all(),
            *self.catalog_epg_widget.get_catalog().all(),
        ])
        save_catalog(Path(config_path), catalog)
        new_config = self.get_target_config()
        # las selecciones se serializan en el dict para save_config
        new_config["m3u_refs"] = list(
            self._catalog_refs(self.catalog_m3u_widget, "m3u"))
        new_config["epg_refs"] = list(
            self._catalog_refs(self.catalog_epg_widget, "epg"))
        targets = {0: new_config}
        save_config(Path(config_path), {"targets": targets})
        # El daemon también sirve este target; si está vivo hay que avisarle,
        # si no seguirá con los filtros que leyó al arrancar.
        notified = False
        try:
            notified = self.daemon_panel.notify_daemon()
        except Exception:  # noqa: BLE001 - notificar nunca puede tumbar el guardado
            notified = False
        self.result_label.setText(
            f"Configuración guardada: {new_config['name']} "
            f"(puerto {new_config.get('port')})."
            + (" Demonio avisado: recargando."
               if notified else " No se notificó al demonio (no responde)."))

    # ------------------------------------------------------------------
    def _source_mode(self) -> str:
        return str(self.source_mode_combo.currentData())

    def _resolve_source_path(self) -> int | str | None:
        """Origen M3U efectivo: índice de lista (app) o ruta/URL (custom)."""
        if self._source_mode() == "custom":
            path = self.custom_m3u_edit.text().strip()
            return path or None
        return self._selected_source()

    def _source_m3u_path(self) -> str | None:
        """Ruta/URL M3U real: el campo custom en modo custom, el m3u de la fuente en app."""
        if self._source_mode() == "custom":
            return self.custom_m3u_edit.text().strip() or None
        idx = self._selected_source()
        if idx is None:
            return None
        return str(self._sources.get(idx, {}).get("m3u", "")) or None

    def _resolve_epg_path(self) -> str | None:
        """Ruta/URL EPG efectiva según el modo de origen elegido."""
        if self._source_mode() == "custom":
            path = self.custom_epg_edit.text().strip()
            return path or None
        epg_idx = self._selected_epg()
        if epg_idx is None:
            return None
        return str(self._sources.get(epg_idx, {}).get("epg", "")) or None

    def _selected_source(self) -> int | None:
        data = self.source_combo.currentData()
        return int(data) if data is not None else None

    def _selected_epg(self) -> int | None:
        data = self.epg_combo.currentData()
        return None if data is None or int(data) < 0 else int(data)

    def _catalog_refs(self, widget, kind: str) -> tuple:
        """SourceRefs de las filas activas del widget de cartera."""
        from src.application.services.list_manager.models import SourceRef
        catalog = widget.get_catalog()
        return tuple(
            SourceRef(e.id, e.name, e.location)
            for e in catalog.all() if e.kind == kind and e.enabled
        )

    def _build_target(self) -> PipelineTarget:
        # multi-fuente: hay filas activas en la cartera => unión;
        # si no, flujo legacy de fuente única (custom_m3u/custom_epg)
        m3u_refs = self._catalog_refs(self.catalog_m3u_widget, "m3u")
        epg_refs = self._catalog_refs(self.catalog_epg_widget, "epg")
        return PipelineTarget(
            name=self.name_edit.text().strip() or "target",
            # La clave de cache es el propio origen: índice (app) o ruta (custom).
            source_index=("merged" if m3u_refs
                          else self._resolve_source_path() or 0),
            rules=tuple(self.rules_widget.get_rules()),
            epg_source_index=self._selected_epg(),
            epg_overrides=self.epg_mapping.get_overrides(),
            port=self.port_spin.value(),
            source_refs=m3u_refs,
            epg_refs=epg_refs,
        )

    def _run_pipeline(self):
        cache_key = self._resolve_source_path()
        m3u_path = self._source_m3u_path()
        if cache_key is None or m3u_path is None:
            return None
        epg_path = self._resolve_epg_path()
        self._pipeline.register_source(cache_key, m3u_path, epg_path)
        try:
            return self._pipeline.run(self._build_target())
        except Exception as e:
            logger.error("Error ejecutando pipeline: %s", e)
            QMessageBox.warning(self, "Gestor de listas", f"Error al procesar la lista:\n{e}")
            return None

    def _browse_m3u(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar lista M3U", "",
            "Listas M3U (*.m3u *.m3u8);;Todos los ficheros (*)",
        )
        if path:
            self.custom_m3u_edit.setText(path)

    def _browse_epg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar guía EPG (XMLTV)", "",
            "Guías XMLTV (*.xml *.xmltv *.gz);;Todos los ficheros (*)",
        )
        if path:
            self.custom_epg_edit.setText(path)

    def _update_source_mode(self) -> None:
        """Muestra/oculta los campos según el tipo de origen elegido."""
        custom = self._source_mode() == "custom"
        for w in (self.source_combo, self.epg_combo):
            w.setVisible(not custom)
            w.parentWidget()  # no-op; visibilidad la gestiona el form
        for w in (self.custom_m3u_edit.parentWidget(), self.custom_epg_edit.parentWidget()):
            w.setVisible(custom)
        self._refresh_mapping()
        # La fuente ya está cargada: los desplegables de grupo de las reglas
        # se crearon vacíos en el __init__, hay que repoblarlos.
        self.rules_widget.refresh_group_options()

    def available_groups(self) -> list[str]:
        """Grupos reales de la fuente actual (para el desplegable de reglas)."""
        source = self._resolve_source_path()
        if source is None:
            return []
        return self._pipeline.get_groups(source)

    def available_group_fields(self) -> dict[str, list[str]]:
        """Campos de agrupación de la fuente actual con sus valores."""
        source = self._resolve_source_path()
        if source is None:
            return {}
        return self._pipeline.get_group_fields(source)

    def _refresh_mapping(self):
        """Ejecuta el pipeline y actualiza el editor de EPG + resumen."""
        result = self._run_pipeline()
        if result is None:
            return
        epg = self._pipeline.get_epg(self._resolve_source_path() or 0)
        self.epg_mapping.set_epg(epg)
        unmatched = [c for c in result.channels if c.matched_epg_id is None]
        self.epg_mapping.load_unmatched(unmatched, self._config.get("epg_overrides", {}))
        self.result_label.setText(
            f"{result.total_input} canales en la fuente → "
            f"{result.filtered_count} tras filtros → "
            f"{len(unmatched)} sin guía EPG."
        )

    # ------------------------------------------------------------------
    def _toggle_service(self):
        if self._server.is_running:
            self._server.stop()
            self.service_button.setText("&Iniciar servicio")
            return
        result = self._run_pipeline()
        if result is None:
            return
        from src.application.services.list_manager.playlist_builder import (
            build_m3u,
            build_xmltv,
        )

        epg = self._pipeline.get_epg(self._resolve_source_path() or 0)
        playlist = build_m3u(result.channels)
        epg_xml = build_xmltv(result.channels, epg) if epg is not None else ""
        # Si cambió host/puerto respecto al servidor vivo, recrear.
        bind = self.bind_combo.currentData()
        port = self.port_spin.value()
        if self._server.is_running and (
            self._server._host != bind or self._server._port != port
        ):
            self._server.stop()
        self._server._host = bind
        self._server._port = port
        self._server.set_content(playlist, epg_xml)
        if self._server.start():
            self.service_button.setText("&Parar servicio")
            scope = (
                "Toda la red local" if bind == "0.0.0.0"
                else "Solo este equipo"
            )
            QMessageBox.information(
                self, "Gestor de listas",
                f"Servicio iniciado ({scope}):\n{self._server.url_base}/playlist.m3u\n"
                f"{self._server.url_base}/epg.xml",
            )
        else:
            owner = find_port_owner(port, bind)
            QMessageBox.warning(
                self, "Gestor de listas",
                f"El puerto {port} está ocupado"
                + (f" por {owner}." if owner else "."),
            )

    def _check_port(self) -> None:
        """Chequeo en vivo del puerto/acceso seleccionados."""
        if self._server.is_running:
            self.port_status_label.setText("")
            return
        port = self.port_spin.value()
        host = str(self.bind_combo.currentData())
        if not port_in_use(host, port):
            self.port_status_label.setText("")
            return
        owner = find_port_owner(port, host)
        self.port_status_label.setText(
            "⚠ Puerto " + str(port) + " ocupado"
            + (f" por {owner}" if owner else "")
        )

    def get_target_config(self) -> dict:
        """Serializa el estado del diálogo para persistir en config.ini."""
        return {
            "name": self.name_edit.text().strip(),
            "source": self._selected_source(),
            "source_mode": self._source_mode(),
            "custom_m3u": self.custom_m3u_edit.text().strip(),
            "epg_source": self._selected_epg(),
            "custom_epg": self.custom_epg_edit.text().strip(),
            "rules": self.rules_widget.get_rules(),
            "epg_overrides": self.epg_mapping.get_overrides(),
            "port": self.port_spin.value(),
            "bind": self.bind_combo.currentData(),
        }
