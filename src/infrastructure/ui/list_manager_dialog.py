"""UI del gestor de listas: wizard 3 pasos + control del servicio web.

Especificación: odd/tasks/list-manager.md. Los diálogos son delgados: delegan
en el pipeline (application) y en el servidor (infrastructure/web).
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.application.services.list_manager.dummy_epg import (
    BLANK_LABEL,
    DUMMY_LABEL,
    is_dummy_id,
    is_dummy_label,
)
from src.application.services.list_manager.models import (
    FilterField,
    FilterRule,
)
from src.application.services.list_manager.playlist_builder import build_dummy_epg_id
from src.application.services.list_manager.source_catalog import SourceCatalog
from src.infrastructure.client.api_client import ApiClient

logger = logging.getLogger(__name__)

class FilterRulesWidget(QWidget):
    """Tabla editable de reglas de filtro simple (modelo xTeVe interno)."""

    # Cualquier cambio en las reglas (fila, texto, operador, acción, campo):
    # la pestaña lo usa para refrescar la vista previa de canales.
    rules_changed = pyqtSignal()

    def __init__(self, parent=None, on_apply=None, dialog=None):
        super().__init__(parent)
        self._dialog = dialog  # proveedor de grupos (ListManagerDialog)
        # Mientras se reconstruye la tabla (cargar, mover filas) no se avisa:
        # sería un aviso por fila en vez de uno por operación.
        self._suspend_changes = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Operador", "Campo", "Texto", "Acción", "Mayús./minús.", ""])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)
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

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Edición del texto de una regla (columna Texto)."""
        if item is not None and item.column() == 2:
            self._notify_changed()

    def _notify_changed(self) -> None:
        """Avisa de un cambio real en las reglas (salvo durante un rebuild)."""
        if not self._suspend_changes:
            self.rules_changed.emit()

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
        op_combo.currentIndexChanged.connect(self._notify_changed)
        self.table.setCellWidget(row, 0, op_combo)

        field_combo = QComboBox()
        self._fill_field_combo(field_combo)
        self._select_field(field_combo, field)
        field_combo.currentIndexChanged.connect(
            lambda _i, r=row: self._on_field_changed(r))
        field_combo.currentIndexChanged.connect(self._notify_changed)
        self.table.setCellWidget(row, 1, field_combo)

        self._set_text_cell(row, field, text)

        action_combo = QComboBox()
        action_combo.addItem("Incluir", False)
        action_combo.addItem("Excluir", True)
        action_combo.setCurrentIndex(1 if exclude else 0)
        action_combo.currentIndexChanged.connect(self._notify_changed)
        self.table.setCellWidget(row, 3, action_combo)

        cs_check = QCheckBox("Respetar mayús./minús.")
        cs_check.setChecked(case_sensitive)
        cs_check.toggled.connect(self._notify_changed)
        self.table.setCellWidget(row, 4, cs_check)

        self.table.setCellWidget(row, 5, self._build_arrows_widget(row))
        self._notify_changed()

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._notify_changed()

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
        self._notify_changed()

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
        self._suspend_changes = True
        self.table.setRowCount(0)
        for state in rows:
            self._add_row(
                field=state["field"], text=state["text"],
                exclude=state["exclude"], case_sensitive=state["case"],
                operator=state["operator"],
            )
        self._suspend_changes = False
        self.table.setCurrentCell(target, 2)
        self._notify_changed()

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
        combo.currentTextChanged.connect(self._notify_changed)
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
        self._suspend_changes = True
        self._set_text_cell(row, "group-title", group)
        combo = self.table.cellWidget(row, 1)
        if combo is not None:
            self._select_field(combo, "group-title")
        self._suspend_changes = False
        self._notify_changed()

    def load_rules(self, rules: list[FilterRule]) -> None:
        self._suspend_changes = True
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
        self._suspend_changes = False
        self._notify_changed()

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


class QualityPriorityWidget(QWidget):
    """Tabla «calidad → prioridad» del target (0 = nunca, 1 = último recurso).

    Los tokens son abiertos: además de los de fábrica se pueden añadir otros
    (``h265``, ``fhd``…). El token vacío se muestra como «(sin etiqueta)».
    """

    changed = pyqtSignal()

    SIN_ETIQUETA = "(sin etiqueta)"
    COL_CALIDAD, COL_PRIORIDAD = 0, 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Calidad", "Prioridad"])
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(self.COL_CALIDAD,
                                        QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(self.COL_PRIORIDAD,
                                        QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(lambda _item: self.changed.emit())
        layout.addWidget(self.table)
        botones = QHBoxLayout()
        add_btn = QPushButton("&Añadir calidad")
        add_btn.clicked.connect(lambda: self._add_row("", 0))
        reset_btn = QPushButton("&Restaurar")
        reset_btn.clicked.connect(self.reset)
        botones.addWidget(add_btn)
        botones.addWidget(reset_btn)
        botones.addStretch(1)
        layout.addLayout(botones)
        self.reset()

    # -- filas ---------------------------------------------------------------
    def _add_row(self, token: str, peso: int) -> None:
        fila = self.table.rowCount()
        self.table.insertRow(fila)
        self.table.setItem(fila, self.COL_CALIDAD, QTableWidgetItem(token))
        self.table.setItem(fila, self.COL_PRIORIDAD, QTableWidgetItem(str(peso)))

    def set_values(self, values: dict[str, int]) -> None:
        """Pinta la tabla; vacío = prioridades de fábrica."""
        from src.application.services.list_manager.quality import DEFAULT_PRIORITY

        tabla = values or DEFAULT_PRIORITY
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for token, peso in tabla.items():
                self._add_row(self.SIN_ETIQUETA if token == "" else str(token),
                              int(peso))
        finally:
            self.table.blockSignals(False)

    def values(self) -> dict[str, int]:
        """Token -> peso; las filas sin calidad se ignoran."""
        out: dict[str, int] = {}
        for fila in range(self.table.rowCount()):
            token_item = self.table.item(fila, self.COL_CALIDAD)
            peso_item = self.table.item(fila, self.COL_PRIORIDAD)
            token = (token_item.text() if token_item else "").strip()
            if not token:
                continue
            if token == self.SIN_ETIQUETA:
                token = ""
            try:
                peso = int((peso_item.text() if peso_item else "0").strip())
            except ValueError:
                peso = 0
            out[token] = max(0, min(9, peso))
        return out

    def reset(self) -> None:
        """Vuelve a las prioridades de fábrica."""
        self.set_values({})
        self.changed.emit()


class _EpgComboWidget(QWidget):
    """Widget con un QComboBox editable para elegir canal EPG.

    El combo muestra el valor actual asignado y al pulsar la flecha
    despliega la lista completa de canales EPG disponibles.
    """

    # Texto elegido por el usuario (etiqueta o id). `activated` cubre la
    # selección de la lista; el `editingFinished` del lineEdit, lo escrito a mano.
    assignment_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.setFixedHeight(24)
        self._combo.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.addWidget(self._combo)
        self._combo.activated.connect(
            lambda _i: self.assignment_changed.emit(self.current_value()))
        # PyQt6 6.11 no trae `QComboBox.editTextFinished`: lo escrito a mano se
        # detecta por el lineEdit (Enter o perder el foco).
        self._combo.lineEdit().editingFinished.connect(
            lambda: self.assignment_changed.emit(self.current_value()))

    def _populate(self, names: list[str]) -> None:
        """Añade opciones al desplegable, preservando el texto actual.

        `setEditText` es la única forma de dejar el combo en blanco: en un combo
        editable `setCurrentIndex(-1)` pone el índice en -1 pero el line edit
        conserva la última opción, que es justo lo que no debe verse en una fila
        sin guía asignada.
        """
        current = self._combo.currentText().strip()
        self._combo.clear()
        self._combo.addItem(BLANK_LABEL)
        self._combo.addItems(names)
        if not current:
            self._combo.setEditText(BLANK_LABEL)
        elif self._combo.findText(current) != -1:
            self._combo.setCurrentIndex(self._combo.findText(current))
        else:
            # texto persistido fuera del catálogo: se conserva editable
            self._combo.setCurrentIndex(0)
            self._combo.setEditText(current)

    def set_items(self, names: list[str]) -> None:
        """Carga las opciones del EPG."""
        self._populate(names)

    def set_current(self, value: str) -> None:
        """Muestra el valor actual asignado sin borrar las opciones."""
        # Primero establecer el texto visible
        self._combo.setEditText(value)
        # Si ya hay items cargados, intentar seleccionar el que coincida
        if value and self._combo.findText(value, Qt.MatchFlag.MatchExactly) != -1:
            self._combo.setCurrentIndex(self._combo.findText(value))

    def current_value(self) -> str:
        """Texto de la opción seleccionada (o editada por el usuario)."""
        return self._combo.currentText().strip()


class EpgMappingWidget(QWidget):
    """Editor manual de asociación EPG con control de activación por canal.

    Cada fila (canal que necesita atención) tiene:
      - columna 'EPG asignado': nombre del canal XMLTV **sin el sufijo '.TV'**
        (solo visual: lo que se persiste es el channel-id real), o 'Dummy 1h'
        para una guía de relleno.
      - columna 'Activo': checkbox; solo se puede activar si el canal tiene
        un EPG asignado. Al borrar la asignación, el canal se desactiva solo.
    """

    # La casilla «solo los que necesitan atención» cambia qué se pide al
    # daemon: el diálogo escucha y repuebla.
    filter_changed = pyqtSignal()

    # Columnas de la tabla
    COL_NAME, COL_URL, COL_LOGO, COL_EPG, COL_ACTIVE = range(5)

    # La listas XMLTV tipo TV.xml escriben el sufijo separado por punto
    # ('La1.TV', 'Antena3.Internacional.TV'). Solo se quita ese sufijo exacto:
    # 'La1TV' no lo es y 'HD'/'Internacional' no son ruido que podamos borrar
    # con seguridad.
    _SUFFIX_RE = re.compile(r"\.TV$", re.IGNORECASE)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel(
            "Canales y su guía EPG. Elige el canal XMLTV en 'EPG asignado', o "
            "'Dummy 1h' para servirlos con una guía de relleno de 1 h. Un canal "
            "sin asignación queda fuera de la lista; desmarca 'Activo' para "
            "sacarlo."
        )
        layout.addWidget(self.info_label)

        # Por defecto se ven todos los canales (también los ya servidos con
        # guía): si no, un canal que funciona no se puede revisar ni cambiar.
        self.only_pending_check = QCheckBox("Solo los que necesitan atención")
        self.only_pending_check.setToolTip(
            "Oculta los canales que ya se sirven con su guía.")
        self.only_pending_check.stateChanged.connect(
            lambda _state: self.filter_changed.emit())
        layout.addWidget(self.only_pending_check)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Canal M3U", "(url)", "Logo", "EPG asignado", "Activo"]
        )
        self.table.setColumnHidden(self.COL_URL, True)
        self.table.horizontalHeader().setStretchLastSection(False)
        # Todas las columnas redimensionables a mano (arrastrando el borde en
        # la cabecera): los nombres de canal y las guías no siempre caben.
        header = self.table.horizontalHeader()
        for columna in (self.COL_NAME, self.COL_LOGO, self.COL_EPG,
                        self.COL_ACTIVE):
            header.setSectionResizeMode(columna,
                                        QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.COL_NAME, 320)
        self.table.setColumnWidth(self.COL_LOGO, 56)
        self.table.setColumnWidth(self.COL_EPG, 220)
        self.table.setColumnWidth(self.COL_ACTIVE, 72)
        header.setMinimumSectionSize(40)
        # Selección por filas y múltiple (Ctrl/Shift): el menú contextual actúa
        # sobre todas las filas seleccionadas.
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        layout.addWidget(self.table)
        # channel-id -> guía(s) que lo anuncian ('Doble_M', 'Koala'…). Vacío en
        # fuentes con una sola guía o sin datos: entonces no hay sufijo.
        self._epg_lists: dict[str, tuple[str, ...]] = {}
        self._names: dict[str, str] = {}
        # etiqueta del desplegable (con sufijo de guía) -> channel-id del XMLTV
        self._epg_labels: dict[str, str] = {}
        self._epg_names = {}
        # True si hay fuente EPG: es lo que activa la regla de servidor
        # ('sin guía no se sirve'). Sin fuente, la casilla 'Activo' sigue
        # siendo solo una decisión del usuario.
        self._require_epg = False
        # urls de la última carga: 'Dummy 1h' necesita la url para derivar su id
        # sintético, y la casilla 'Activo' se recalea al cambiar el desplegable.
        self._row_urls: list[str] = []
        # asignaciones vigentes al repoblar: las de los canales que no están en
        # la tabla (ya servidos con guía) no se pueden reconstruir de las filas
        # y se perderían al guardar si no se conservan aquí.
        self._persisted_overrides: dict[str, str] = {}
        # urls apagadas a mano (las que se persisten en disabled_channels)
        self._manual_off: set[str] = set()
        # url -> id que el pipeline resolvió solo (sin override manual)
        self._auto_ids: dict[str, str] = {}
        # url -> si el daemon la sirve ahora mismo (None = no lo dijo). Un
        # duplicado de calidad tiene guía pero no se sirve.
        self._daemon_served: dict[str, bool | None] = {}
        # filas que el usuario acaba de tocar: su estado local manda sobre el
        # último pipeline, que aún no se ha reejecutado
        self._touched: set[str] = set()
        # mientras se repuebla la tabla se mueven casillas por programa: hay que
        # ignorar esos itemChanged, o convertirían un estado derivado en una
        # decisión del usuario.
        self._loading = False
        self.table.itemChanged.connect(self._on_item_changed)

    @property
    def _epg_names(self) -> dict[str, str]:
        """Catálogo XMLTV: channel-id -> display-name."""
        return self._names

    @_epg_names.setter
    def _epg_names(self, value: dict[str, str] | None) -> None:
        # las etiquetas visibles dependen de este catálogo: se reconstruyen al
        # asignarlo (nunca quedan desincronizadas) con o sin sufijo de guía.
        self._names = dict(value or {})
        self._load_labels()

    @classmethod
    def _clean(cls, label: str) -> str:
        """Etiqueta visible de un canal XMLTV: sin el sufijo '.TV'."""
        return cls._SUFFIX_RE.sub("", label or "").strip()

    def _dummy_offered(self) -> bool:
        """True si la opción 'Dummy 1h' puede ofrecerse sin ambigüedad.

        Si la guía trae un canal cuyo nombre o id es literalmente 'Dummy 1h',
        la opción se retira: dos entradas con el mismo texto no son
        distinguibles en el desplegable, y un canal real con guía vale más que
        la síntetica. Como efecto secundario aceptable, un id dummy persistido
        antes de que apareciera ese canal se reinterpreta hacia el canal real.
        """
        candidates = set(self._epg_names) | {self._clean(d) for d in self._epg_names.values()}
        return not any(is_dummy_label(c) for c in candidates)

    def only_pending(self) -> bool:
        """True si la tabla debe ocultar los canales ya servidos con guía."""
        return self.only_pending_check.isChecked()

    def epg_channel_names(self) -> list[str]:
        """Opciones del desplegable: 'Dummy 1h' primero y luego la guía.

        Se listan la etiqueta limpia y el channel-id: en TV.xml ambos coinciden
        ('La1'), pero otras fuentes usan ids distintos del nombre ('es.la1') y
        poder pegar el id sigue siendo útil. Cuando la EPG es una unión de
        guías, cada opción lleva la lista que la anuncia ('La 1 · Doble_M').
        """
        names = sorted({label for label in self._epg_labels if label})
        return ([DUMMY_LABEL] + names) if self._dummy_offered() else names

    def _label(self, channel_id: str, display: str) -> str:
        """Etiqueta del desplegable: nombre limpio + guía(s) que lo anuncian."""
        lists = self._epg_lists.get(channel_id) or ()
        suffix = f" · {' + '.join(lists)}" if lists else ""
        return f"{display}{suffix}"

    def _load_labels(self) -> None:
        """Reconstruye etiqueta -> channel-id tras cargar el catálogo.

        Es la fuente de verdad de la resolución: el texto visible puede llevar
        sufijo de guía y no coincide ni con el id ni con el display-name.
        """
        labels: dict[str, str] = {}
        for channel_id, display in self._epg_names.items():
            variants = [self._clean(display)]
            if channel_id != display:
                # id distinto del nombre ('es.la1'): poder pegar el id ayuda
                variants.append(channel_id)
            for text in variants:
                if text:
                    labels.setdefault(self._label(channel_id, text), channel_id)
        self._epg_labels = labels

    def set_epg(self, epg) -> None:
        """Carga el catálogo XMLTV que ofrecerán los desplegables de la tabla."""
        self._epg_lists = {
            cid: tuple(lists) for cid, lists
            in (getattr(epg, "epg_lists", None) or {}).items() if lists}
        self._epg_names = dict(epg.channel_names) if epg is not None else {}
        self._require_epg = epg is not None

    def _resolve_epg_id(self, value: str) -> str:
        """Nombre visible (con o sin sufijo o id del EPG -> channel-id real.

        ``DUMMY_LABEL`` se devuelve intacto: traducirlo al id sintético pide la
        url del canal, y eso solo lo sabe ``get_overrides``.

        Un valor que no está en el catálogo se conserva tal cual: permite
        escribir a mano un tvg-id que la EPG no anuncia.
        """
        value = (value or "").strip()
        if not value:
            return ""
        if is_dummy_label(value) and self._dummy_offered():
            return DUMMY_LABEL
        if value in self._epg_names:
            return value
        if value in self._epg_labels:
            return self._epg_labels[value]
        # el usuario pudo editar el texto y comerse/pisar el sufijo de guía
        base = value.split(" · ", 1)[0].strip()
        if base in self._epg_labels:
            return self._epg_labels[base]
        if base in self._epg_names:
            return base
        for channel_id, display in self._epg_names.items():
            if base in (display, self._clean(display)):
                return channel_id
        return value

    def _display_label(self, channel_id: str) -> str:
        """Canal-id persistido -> etiqueta legible para el desplegable."""
        if is_dummy_id(channel_id):
            return DUMMY_LABEL
        display = self._epg_names.get(channel_id)
        return self._label(channel_id, self._clean(display) if display
                           else channel_id)

    def load_unmatched(self, channels, overrides: dict[str, str],
                       disabled: tuple[str, ...] = ()) -> None:
        """Rellena la tabla con los canales que necesitan atención.

        ``disabled``: urls apagadas a mano (persistidas). Es la única fuente de
        ``disabled_channels``; una fila sin guía se marca sola, pero no se
        guarda como decisión del usuario.
        """
        self._loading = True
        try:
            self._manual_off = set(disabled)
            self._persisted_overrides = dict(overrides)
            # id que el pipeline resolvió solo (sin override): no se persiste
            # como asignación manual si el usuario no lo cambia.
            self._auto_ids = {ch.url: (ch.matched_epg_id or "")
                              for ch in channels}
            self._daemon_served = {ch.url: getattr(ch, "served", None)
                                   for ch in channels}
            self._touched = set()
            self._row_urls = [ch.url for ch in channels]
            names = self.epg_channel_names()
            self.table.setRowCount(0)
            self.table.setRowCount(len(channels))
            for row, ch in enumerate(channels):
                name_item = QTableWidgetItem(ch.name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if getattr(ch, "duplicate", False):
                    name_item.setToolTip(
                        "Variante descartada por prioridad de calidad: se sirve "
                        "otra variante de este canal. Desactiva esa otra si "
                        "prefieres esta.")
                self.table.setItem(row, self.COL_NAME, name_item)

                url_item = QTableWidgetItem(ch.url)
                self.table.setItem(row, self.COL_URL, url_item)

                # Logo: placeholder; el servicio de logos rellena la celda async.
                logo_item = QTableWidgetItem()
                logo_item.setData(Qt.ItemDataRole.UserRole, ch.logo_url or "")
                logo_item.setFlags(logo_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, self.COL_LOGO, logo_item)

                # se muestra la guía efectiva: la asignada a mano o la que
                # resolvió el pipeline (un canal servido ya tiene la suya)
                assigned = overrides.get(ch.url) or ch.matched_epg_id or ""
                epg_combo = _EpgComboWidget(self)
                epg_combo.set_items(names)
                epg_combo.set_current(self._display_label(assigned))
                epg_combo.assignment_changed.connect(
                    lambda _text, r=row: self._on_assignment_changed(r))
                self.table.setCellWidget(row, self.COL_EPG, epg_combo)

                active_item = QTableWidgetItem()
                active_item.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                active_item.setCheckState(
                    Qt.CheckState.Checked if self._served(ch.url, bool(assigned))
                    else Qt.CheckState.Unchecked
                )
                self.table.setItem(row, self.COL_ACTIVE, active_item)
        finally:
            self._loading = False

    # ------------------------------------------------------------------
    # Casilla 'Activo'
    # ------------------------------------------------------------------
    def _served(self, url: str, has_assignment: bool) -> bool:
        """Estado que debe mostrar la casilla.

        Con fuente EPG el servidor descarta todo canal sin guía, así que la
        casilla no puede decir 'Activo' en una fila sin asignar: mentiría y, al
        guardar, el usuario no entendería por qué el canal sigue fuera. Sin
        fuente EPG no hay regla: la casilla es solo decisión del usuario.
        """
        if url in self._manual_off:
            return False
        if url in self._touched:
            # lo que ve el usuario manda: el pipeline aún no ha reejecutado
            return True if not self._require_epg else has_assignment
        servido = self._daemon_served.get(url)
        if servido is not None:
            return servido
        return True if not self._require_epg else has_assignment

    def selected_rows(self) -> list[int]:
        """Filas seleccionadas, ordenadas (admite Ctrl/Shift)."""
        model = self.table.selectionModel()
        if model is None:
            return []
        return sorted({index.row() for index in model.selectedRows()})

    def set_rows_enabled(self, rows: list[int], enabled: bool) -> None:
        """Activa o desactiva varios canales, como la casilla pero en bloque.

        Mismas reglas que ``_on_item_changed``: el apagado manual solo se
        registra en filas que de otra forma se servirían, y reactivar una fila
        sin guía no la sirve (manda la regla del servidor).
        """
        self._loading = True
        try:
            for row in rows:
                if not 0 <= row < len(self._row_urls):
                    continue
                url = self._row_urls[row]
                has_assignment = bool(self._row_assignment(row))
                if enabled:
                    self._manual_off.discard(url)
                elif self._served(url, has_assignment):
                    self._manual_off.add(url)
                self._set_active(row, url, has_assignment)
        finally:
            self._loading = False

    def row_menu(self, rows: list[int]) -> QMenu:
        """Menú del botón derecho: habilitar o deshabilitar lo seleccionado."""
        menu = QMenu(self.table)
        cuantos = "1 canal" if len(rows) == 1 else f"{len(rows)} canales"
        cabecera = QAction(cuantos, menu)
        cabecera.setEnabled(False)
        menu.addAction(cabecera)
        menu.addSeparator()
        for texto, habilitar in (("Habilitar", True), ("Deshabilitar", False)):
            accion = QAction(texto, menu)
            accion.triggered.connect(
                lambda _checked=False, valor=habilitar:
                self.set_rows_enabled(rows, valor))
            menu.addAction(accion)
        return menu

    def _show_row_menu(self, pos) -> None:
        """Menú contextual sobre la fila pulsada (o sobre la selección)."""
        if self._loading:
            return
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        if index.row() not in self.selected_rows():
            # clic derecho fuera de la selección: opera sobre esa fila
            self.table.selectRow(index.row())
        filas = self.selected_rows()
        if filas:
            self.row_menu(filas).exec(
                self.table.viewport().mapToGlobal(pos))

    def _row_assignment(self, row: int) -> str:
        """Channel-id (o 'Dummy 1h') elegido en la fila, '' si está vacía."""
        combo = self.table.cellWidget(row, self.COL_EPG)
        if not isinstance(combo, _EpgComboWidget):
            return ""
        return self._resolve_epg_id(combo.current_value())

    def _on_assignment_changed(self, row: int) -> None:
        """El usuario ha tocado el desplegable: la casilla le sigue.

        Asignar guía (real o Dummy) limpia el apagado manual: si no, elegir
        'Dummy 1h' parecería no hacer nada porque el canal seguiría fuera.
        """
        if self._loading or row >= len(self._row_urls):
            return
        url = self._row_urls[row]
        self._touched.add(url)
        assigned = self._row_assignment(row)
        if assigned:
            self._manual_off.discard(url)
        self._set_active(row, url, bool(assigned))

    def _set_active(self, row: int, url: str, has_assignment: bool) -> None:
        item = self.table.item(row, self.COL_ACTIVE)
        if item is None:
            return
        self._loading = True
        try:
            item.setCheckState(
                Qt.CheckState.Checked if self._served(url, has_assignment)
                else Qt.CheckState.Unchecked
            )
        finally:
            self._loading = False

    def _on_item_changed(self, item) -> None:
        """La casilla es el único mando del apagado manual.

        Se registran solo los cambios del usuario: los movimientos por programa
        (repoblado, recaleo de 'Activo') no son decisiones y no deben escribirse
        en ``disabled_channels``.
        """
        if self._loading or item.column() != self.COL_ACTIVE:
            return
        row = item.row()
        if row >= len(self._row_urls):
            return
        url = self._row_urls[row]
        self._touched.add(url)
        has_assignment = bool(self._row_assignment(row))
        if item.checkState() == Qt.CheckState.Checked:
            self._manual_off.discard(url)
        elif self._served(url, has_assignment):
            # Solo tiene sentido registrar el apagado en una fila que de otra
            # forma se serviría: apagar una fila ya fuera por falta de guía no
            # es una decisión nueva, es la misma casilla de siempre.
            self._manual_off.add(url)
        # Reactivar una fila sin guía no es posible: la casilla vuelve a su
        # estado real y el usuario entiende que primero hay que asignar EPG.
        self._set_active(row, url, has_assignment)

    def get_overrides(self) -> dict[str, str]:
        """Devuelve url -> channel-id asignado manualmente (solo los con valor).

        'Dummy 1h' se traduce aquí, que es el único sitio donde se conoce la url
        del canal: el id sintético es propio de cada canal.

        Las asignaciones de canales que no están en la tabla se conservan tal
        cual: la tabla puede estar filtrada, así que no puede reconstruirlas.
        Una fila visible con el desplegable en blanco sí borra la asignación:
        es la forma de quitarla desde la UI.

        Tampoco se materializa la guía que el pipeline resolvió solo: si el
        usuario no toca el desplegable, el canal sigue casando por ``tvg-id`` y
        el fichero no se llena de overrides redundantes.
        """
        visibles = set(self._row_urls)
        overrides: dict[str, str] = {
            url: cid for url, cid in self._persisted_overrides.items()
            if url not in visibles
        }
        for row in range(self.table.rowCount()):
            url_item = self.table.item(row, self.COL_URL)
            combo = self.table.cellWidget(row, self.COL_EPG)
            if url_item is None or not isinstance(combo, _EpgComboWidget):
                continue
            cid = self._resolve_epg_id(combo.current_value())
            if cid == DUMMY_LABEL:
                name_item = self.table.item(row, self.COL_NAME)
                cid = build_dummy_epg_id(url_item.text(),
                                         name_item.text() if name_item else "")
            url = url_item.text()
            if (cid and cid == self._auto_ids.get(url)
                    and url not in self._persisted_overrides):
                continue  # ya lo resolvía el pipeline: no es un mapeo manual
            if cid:
                overrides[url] = cid
        return overrides

    def get_disabled(self) -> tuple[str, ...]:
        """Urls apagadas a mano, en el orden de la tabla.

        Son las que se persisten en ``disabled_channels``. Una fila sin guía
        aparece desmarcada pero **no** está aquí: se descarta por la regla de
        servidor, no por decisión del usuario, y persistirla como apagada
        impediría rescatarla con Dummy 1h (el apagado manual se aplica antes
        que la EPG).
        """
        disabled: list[str] = []
        for row in range(self.table.rowCount()):
            url_item = self.table.item(row, self.COL_URL)
            if url_item is None or url_item.text() not in self._manual_off:
                continue
            disabled.append(url_item.text())
        return tuple(disabled)

    def set_logo(self, url: str, pixmap=None) -> None:
        """Rellena la celda de logo de todas las filas con esa url (async)."""
        if not url:
            return
        for row in range(self.table.rowCount()):
            logo_item = self.table.item(row, self.COL_LOGO)
            if logo_item is None or logo_item.data(Qt.ItemDataRole.UserRole) != url:
                continue
            if pixmap is not None:
                logo_item.setIcon(QIcon(pixmap))
            logo_item.setText("")

    def logo_urls(self) -> list[str]:
        """Urls de logo únicas presentes en la tabla (para precarga async)."""
        urls: list[str] = []
        seen: set[str] = set()
        for row in range(self.table.rowCount()):
            logo_item = self.table.item(row, self.COL_LOGO)
            if logo_item is None:
                continue
            u = logo_item.data(Qt.ItemDataRole.UserRole)
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        return urls


def _source_payload(entry) -> dict:
    """SourceEntry → dict para PUT /api/v1/sources."""
    return {"id": entry.id, "name": entry.name, "kind": entry.kind,
            "source_type": entry.source_type, "location": entry.location,
            "refresh_min": entry.refresh_min, "enabled": entry.enabled}


class ListManagerDialog(QDialog):
    """Diálogo principal del gestor: wizard 3 pasos + servicio web."""

    # Resultado del guardado en segundo plano (dict). Se emite desde el hilo
    # de trabajo y Qt lo entrega en el de la GUI, donde sí se tocan widgets.
    save_finished = pyqtSignal(object)

    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.setWindowTitle("weteve — Gestor de listas")
        # Es un QDialog que se usa como ventana principal (main.py hace
        # exec()): sin estos hints la barra de título solo trae cerrar.
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self._config = self._with_legacy_fields(config)
        # True mientras se aplica un cambio leído de disco: en ese momento las
        # asignaciones/desactivados válidos son los del fichero, no los de la
        # tabla (que todavía tiene el estado viejo).
        self._reloading = False
        # Guardado en segundo plano: el pipeline del demonio tarda segundos y
        # hacerlo en el hilo de la GUI congelaba la ventana.
        self._saving = False
        self._save_pending: dict | None = None
        self._save_pool = ThreadPoolExecutor(max_workers=1,
                                            thread_name_prefix="weteve-save")
        self._save_button = None
        # La GUI es un frontend: ni pipeline ni servidor propios. Todo lo que
        # sabe del motor lo pregunta por la API REST del daemon.
        self._api: ApiClient | None = None
        self._api_ready = False
        # El widget de reglas pregunta grupos/campos por cada fila: se cachean
        # (se invalidan al cambiar cartera o al refrescar).
        self._groups_cache: list[str] | None = None
        self._group_fields_cache: dict[str, list[str]] | None = None

        layout = QVBoxLayout(self)

        # Pestañas: cada sección ocupa todo el alto disponible. «Estado» va
        # primero: semáforo del demonio y URLs de acceso de cada servicio.
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        from src.infrastructure.ui.status_panel import StatusPanel

        self.status_panel = StatusPanel(
            client_factory=self._api_client,
            target_id=str(config.get("target_id", "0")),
            parent=self,
        )
        self.tabs.addTab(self.status_panel, "Estado")

        # Cartera de fuentes: dos pestañas con la misma forma; "Activa"
        # marca qué filas aporta este target a la unión.
        from src.infrastructure.client.sources_store import load_catalog
        from src.infrastructure.ui.source_catalog_widget import SourceCatalogWidget
        config_path = config.get("_config_path")
        catalog = SourceCatalog()
        if config_path:
            # solo lectura: la migración de configs antiguas la hace el demonio
            try:
                catalog = load_catalog(Path(config_path))
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
        # Orden: primero se filtra la lista IPTV («Filtros iptv») y después se
        # elige con qué guía se mapea («Listas EPG», «Mapeado IPTV <-> EPG»).
        # Filtro de canales: reglas a la izquierda (80%) y, a la derecha
        # (20%), vista previa en vivo de los canales que quedan al aplicar
        # el filtro.
        self.tab_filters = QWidget(self)
        filter_layout = QHBoxLayout(self.tab_filters)
        rules_column = QWidget(self.tab_filters)
        rules_layout = QVBoxLayout(rules_column)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_widget = FilterRulesWidget(on_apply=self._refresh_mapping,
                                              dialog=self)
        self.rules_widget.load_rules(config.get("rules", []))
        rules_layout.addWidget(self.rules_widget)
        filter_layout.addWidget(rules_column, 4)
        filter_layout.addWidget(self._build_filter_preview(), 1)
        # Los cambios en las reglas se agrupan con un temporizador: teclear no
        # debe recalcular la vista previa en cada pulsación.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._refresh_filter_preview)
        self.rules_widget.rules_changed.connect(self._schedule_filter_preview)
        self.tabs.addTab(self.tab_filters, "Filtros iptv")
        self.tabs.addTab(self.catalog_epg_widget, "Listas EPG")

        # Mapeado canal<-->epg
        self.tab_mapping = QWidget(self)
        epg_layout = QVBoxLayout(self.tab_mapping)
        self.epg_mapping = EpgMappingWidget()
        self.epg_mapping.filter_changed.connect(self._refresh_mapping)
        epg_layout.addWidget(self.epg_mapping)
        self.result_label = QLabel("")
        epg_layout.addWidget(self.result_label)
        self.tabs.addTab(self.tab_mapping, "Mapeado IPTV <-> EPG")

        # Descarga asíncrona de logos con caché en disco (TTL 7 días).
        from src.infrastructure.ui.logo_cache_service import LogoCacheService
        self._logo_service = LogoCacheService(
            Path(__file__).resolve().parents[2] / "cache" / "logos", self)
        self._logo_service.logo_ready.connect(self._on_logo_ready)

        # Servicio web
        service_group = QGroupBox("Servicio web (playlist y EPG)")
        service_form = QFormLayout(service_group)
        self.name_edit = QLineEdit(str(config.get("name", "")))
        service_form.addRow("&Nombre del resultado:", self.name_edit)
        port_row = QHBoxLayout()
        # El puerto es único (el del demonio): aquí se muestra la URL servida.
        self.service_button = QPushButton("&Iniciar servicio")
        self.service_button.clicked.connect(self._toggle_service)
        port_row.addWidget(self.service_button)
        port_row.addStretch()
        service_form.addRow(port_row)
        self.service_url_label = QLabel("")
        self.service_url_label.setWordWrap(True)
        service_form.addRow(self.service_url_label)

        # Pestaña «Configuración»: target + servicio web + demonio ([DAEMON]).
        self.tab_config = QWidget(self)
        config_tab_layout = QVBoxLayout(self.tab_config)
        config_tab_layout.addWidget(service_group)

        # Target activo y su prioridad de calidad (una sola fuente: este
        # fichero, escrito por el demonio).
        target_group = QGroupBox("Target y prioridad de calidad")
        target_form = QFormLayout(target_group)
        self.target_combo = QComboBox()
        self.target_combo.currentIndexChanged.connect(self._on_target_selected)
        target_form.addRow("&Target:", self.target_combo)
        self.priority_widget = QualityPriorityWidget()
        self.priority_widget.setToolTip(
            "De cada canal repetido se sirve la variante con más prioridad. "
            "0 = no servir nunca; 1 = último recurso.")
        target_form.addRow(QLabel(
            "De cada canal repetido (HD/FULL HD/SD) se sirve una variante."))
        target_form.addRow(self.priority_widget)
        config_tab_layout.addWidget(target_group)

        # Sección [DAEMON]: API de control + endpoint MCP. Se persiste aparte
        # (sección [DAEMON] de config.ini) vía save_daemon_config en _on_save.
        from src.application.services.daemon.config import (
            DaemonConfig,
            load_daemon_config,
        )
        if config_path:
            self._daemon_cfg = load_daemon_config(Path(config_path))
        else:
            self._daemon_cfg = DaemonConfig()
        daemon_group = QGroupBox("Demonio (un puerto: control, API y MCP)")
        daemon_form = QFormLayout(daemon_group)
        self.control_host_edit = QLineEdit(self._daemon_cfg.control_host)
        daemon_form.addRow("&Host de control:", self.control_host_edit)
        self.control_port_spin = QSpinBox()
        self.control_port_spin.setRange(1024, 65535)
        self.control_port_spin.setValue(self._daemon_cfg.control_port)
        daemon_form.addRow("Puerto de &control:", self.control_port_spin)
        # Todo (control, REST, MCP y playlists) se sirve en control_port.
        self.mcp_token_edit = QLineEdit(self._daemon_cfg.mcp_token)
        self.mcp_token_edit.setPlaceholderText("Vacío = sin autenticación (solo localhost)")
        daemon_form.addRow("&Token MCP:", self.mcp_token_edit)
        config_tab_layout.addWidget(daemon_group)
        config_tab_layout.addStretch()

        # Demonio: estado y aplicación de la config al servicio permanente
        from src.infrastructure.ui.daemon_panel import DaemonPanel
        self.daemon_panel = DaemonPanel(
            target_id=str(config.get("target_id", "0")),
            config_path=config.get("_client_path") or config.get("_config_path"))
        # El sondeo del demonio (~2 s si está parado) se hace después de que la
        # ventana esté visible: la aplicación debe abrir siempre.
        self.daemon_panel.refresh_later()
        # Solo se para al cerrar lo que arrancó esta ventana: un demonio del
        # Programador de tareas es de otro.
        self._auto_started_daemon = False
        self._daemon_stopped = False
        if self.daemon_panel.autostart_checked():
            self._auto_started_daemon = self.daemon_panel.ensure_running()
        config_tab_layout.addWidget(self.daemon_panel)
        self.tabs.addTab(self.tab_config, "Configuración")

        # Pestaña «Logs»: tail -f de weteve.log y weteve-daemon.log con
        # autoscroll inteligente (se pausa al subir a leer).
        from src.infrastructure.ui.log_tail_widget import LogTailWidget
        logs_dir = Path(__file__).resolve().parents[3] / "logs"
        self.tab_logs = QWidget(self)
        logs_layout = QVBoxLayout(self.tab_logs)
        log_selector_row = QHBoxLayout()
        log_selector_row.addWidget(QLabel("Fichero:"))
        self.log_combo = QComboBox()
        self.log_combo.addItem("weteve", logs_dir / "weteve.log")
        self.log_combo.addItem("daemon", logs_dir / "weteve-daemon.log")
        log_selector_row.addWidget(self.log_combo)
        log_selector_row.addStretch()
        logs_layout.addLayout(log_selector_row)
        self.log_tail = LogTailWidget(self.log_combo.currentData())
        self.log_combo.currentIndexChanged.connect(
            lambda: self.log_tail.set_log_path(self.log_combo.currentData()))
        logs_layout.addWidget(self.log_tail)
        self.tabs.addTab(self.tab_logs, "Logs")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("&Guardar")
        self._save_button = save_btn
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_finished.connect(self._on_save_finished)

        # Primera carga: la tabla de mapeo y los combos de grupo salen del
        # snapshot del daemon (no hay pipeline local).
        self._refresh_mapping()
        self.rules_widget.refresh_group_options()
        self._sync_service_url()
        self.status_panel.refresh()
        self._reload_target_choices()
        self._load_priorities()

        # Refresco desde disco: config.ini lo escriben también el daemon y sus
        # herramientas MCP (listas, filtros, mapeos). Sin vigilarlo, la ventana
        # abierta se quedaría con el estado que leyó al arrancar.
        self._config_signature = self._config_file_signature()
        self._config_watch = QTimer(self)
        self._config_watch.setInterval(1500)
        self._config_watch.timeout.connect(self._reload_config_if_changed)
        self._config_watch.start()

    # -- refresco desde disco -------------------------------------------------

    def _config_file_signature(self) -> tuple[int, int] | None:
        """(mtime, tamaño) de config.ini; None si aún no existe."""
        path = self._config.get("_config_path")
        if path is None:
            return None
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _reload_config_if_changed(self) -> None:
        """Aplica al diálogo los cambios externos de config.ini.

        La firma se guarda antes de aplicar: si el fichero se escribe otra vez
        durante el refresco, el siguiente tic lo detecta y vuelve a aplicar.
        """
        from PyQt6 import sip

        if sip.isdeleted(self):
            return  # el tic llegó después de cerrar el diálogo
        signature = self._config_file_signature()
        if signature == self._config_signature:
            return
        self._config_signature = signature
        self._apply_config_from_disk()

    def _reload_target_choices(self) -> None:
        """Rellena el selector con los targets que conoce el demonio."""
        actual = self._target_id()
        items: list[tuple[str, str]] = []
        client = self._api_client()
        if client is not None:
            code, body = client.targets()
            if code == 200:
                for tid, target in (body.get("targets") or {}).items():
                    items.append((str(tid), str(target.get("name") or tid)))
        if not items:
            items = [(actual, str(self._config.get("name") or actual))]
        self._loading_targets = True
        try:
            self.target_combo.clear()
            for tid, name in sorted(items):
                self.target_combo.addItem(f"{tid} — {name}", tid)
            idx = self.target_combo.findData(actual)
            self.target_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._loading_targets = False

    def _load_priorities(self) -> None:
        """Pinta la prioridad de calidad del target activo (fichero o fábrica)."""
        values = self._config.get("quality_priority") or {}
        path = self._config.get("_config_path")
        if path is not None:
            try:
                from src.infrastructure.client.persistencia import load_config

                fresh = load_config(Path(path)).get("targets", {}).get(
                    int(self._config.get("target_id", 0)), {})
                values = fresh.get("quality_priority") or values
            except Exception:  # noqa: BLE001 - fichero ilegible: de fábrica
                logger.debug("sin prioridades legibles en %s", path,
                             exc_info=True)
        self._config["quality_priority"] = values
        self.priority_widget.set_values(values)

    def _on_target_selected(self, _index: int) -> None:
        """Cambia el target activo de TODA la ventana (reglas, cartera, mapeo)."""
        if getattr(self, "_loading_targets", False):
            return
        target_id = self.target_combo.currentData()
        if target_id is None or str(target_id) == self._target_id():
            return
        self._config["target_id"] = str(target_id)
        self._apply_config_from_disk()
        self.name_edit.setText(str(self._config.get("name") or ""))
        self._load_priorities()
        self._sync_service_url()
        self.status_panel.refresh()

    def _apply_config_from_disk(self) -> None:
        """Relee ``daemon.toml`` y repinta listas, filtros y mapeos."""
        path = self._config.get("_config_path")
        if path is None:
            return
        from src.infrastructure.client.persistencia import load_config
        from src.infrastructure.client.sources_store import load_catalog

        try:
            catalog = load_catalog(Path(path))
            fresh = load_config(Path(path)).get("targets", {}).get(
                int(self._config.get("target_id", 0)), {})
        except Exception:  # noqa: BLE001 - fichero a medio escribir
            logger.warning("No se pudo releer %s", path, exc_info=True)
            return

        # 1) cartera de fuentes (pestañas Listas IPTV y Listas EPG)
        self._invalidate_catalog_cache()
        self.catalog_m3u_widget.set_catalog(catalog)
        self.catalog_epg_widget.set_catalog(catalog)
        self.catalog_m3u_widget.set_selection(
            [r.id for r in fresh.get("m3u_refs", [])])
        self.catalog_epg_widget.set_selection(
            [r.id for r in fresh.get("epg_refs", [])])

        # 2) estado del target: filtros, mapeos y desactivados del fichero
        self._config = {**self._config, **fresh}

        # 3) filtros (recarga una sola vez; encadena la vista previa)
        self.rules_widget.load_rules(fresh.get("rules", []))
        self.rules_widget.refresh_group_options()

        # 4) mapeos: el pipeline se reejecuta con las asignaciones nuevas. La
        # bandera hace que ``_overrides_now``/_disabled_now lean del fichero y
        # no de la tabla, que aún muestra el estado anterior.
        self._reloading = True
        try:
            self._refresh_mapping()
        finally:
            self._reloading = False


    def _on_save(self) -> None:
        """Guarda por la API del demonio (escritor único), sin cerrar.

        Un solo lote con ``reload: false`` y **una** ejecución al final: la
        cartera, la selección de fuentes, los ajustes, las reglas, los mapeos y
        los desactivados. El estado se recoge aquí (hilo de la GUI) y el lote se
        manda desde un hilo de trabajo: el pipeline tarda segundos y bloquear la
        GUI congelaba la ventana. Al terminar, ``save_finished`` devuelve el
        resultado al hilo de la GUI.
        """
        if self._saving:
            return  # ya hay un guardado en marcha: no se encola otro
        config_path = self._config.get("_config_path")
        client = self._api_client()
        if config_path is None or client is None:
            return
        payload = self._save_payload(client, Path(config_path))
        # solo se escribe el fichero del cliente si el diálogo lo conoce:
        # sin él, la sección [DAEMON] no tiene dónde ir
        payload["client_path"] = self._config.get("_client_path")
        self._saving = True
        self._save_pending = payload
        self._config_watch.stop()
        if self._save_button is not None:
            self._save_button.setEnabled(False)
        self.result_label.setText(
            "Guardando en el demonio… (el pipeline tarda unos segundos)")
        fut = self._save_pool.submit(self._save_job, payload)
        fut.add_done_callback(
            lambda f: self.save_finished.emit(f.result())
            if not f.cancelled() else None)

    def save_in_progress(self) -> bool:
        """True mientras el guardado en segundo plano está en marcha."""
        return self._saving

    def _saved_state(self, payload: dict) -> dict:
        """Campos del target tal como se acaban de mandar (sin releer widgets)."""
        return {"name": payload["name"], "port": payload["port"],
                "rules": payload["rules_objects"],
                "quality_priority": payload["quality_priority"],
                "epg_overrides": payload["overrides"],
                "disabled_channels": payload["disabled"]}

    def _save_payload(self, client, config_path: Path) -> dict:
        """Estado a guardar, capturado en el hilo de la GUI.

        Nada de esto se puede leer desde el hilo de trabajo: tocaría widgets.
        """
        from src.application.services.daemon.config import DaemonConfig

        target_id = self._target_id()
        catalog = SourceCatalog([
            *[e for e in self.catalog_m3u_widget.get_catalog().all()
              if e.kind == "m3u"],
            *[e for e in self.catalog_epg_widget.get_catalog().all()
              if e.kind == "epg"],
        ])
        self._invalidate_catalog_cache()
        overrides = self._overrides_now()
        return {
            "client": client,
            "config_path": config_path,
            "target_id": target_id,
            "sources": [_source_payload(e) for e in catalog.all()],
            "m3u_ids": [r.id for r in self._catalog_refs(
                self.catalog_m3u_widget, "m3u")],
            "epg_ids": [r.id for r in self._catalog_refs(
                self.catalog_epg_widget, "epg")],
            "name": self.name_edit.text().strip() or "target",
            "port": self._config.get("port"),
            "rules": [r.serialize() for r in self.rules_widget.get_rules()],
            "rules_objects": self.rules_widget.get_rules(),
            "quality_priority": self.priority_widget.values(),
            "overrides": overrides,
            "mapping_changes": self._mapping_changes(overrides),
            "disabled": list(self._disabled_now()),
            "daemon": DaemonConfig(
                control_host=self.control_host_edit.text().strip() or "127.0.0.1",
                control_port=self.control_port_spin.value(),
                mcp_port=self.control_port_spin.value(),
                mcp_token=self.mcp_token_edit.text().strip(),
                autostart=self.daemon_panel.autostart_checked(),
                stop_on_exit=self.daemon_panel.stop_on_exit_checked(),
            ),
        }

    @staticmethod
    def _save_job(payload: dict) -> dict:
        """Lote HTTP del guardado; se ejecuta en el hilo de trabajo.

        Devuelve ``{"ok": True, "served": n}`` o, si un paso falla,
        ``{"ok": False, "body", "code", "label}`` para que la GUI avise.
        """
        client = payload["client"]
        target_id = payload["target_id"]

        def failure(label: str, code: int, body: dict) -> dict:
            return {"ok": False, "label": label, "code": code,
                    "body": body}

        # 0) si el demonio no responde no se guarda a medias
        code, body = client.target_status(target_id)
        if code == 0:
            return failure("demonio no disponible", code, body)

        # 1) cartera: cada pestaña manda sobre sus filas y solo sobre ellas
        code, body = client.set_sources(payload["sources"])
        if code == 0 or code >= 400 or "error" in body:
            return failure("cartera de fuentes", code, body)

        # 2) resto de cambios del target, sin recargar cada uno
        steps = [
            ("fuentes del target", client.set_target_sources(
                target_id, payload["m3u_ids"], payload["epg_ids"],
                reload=False)),
            ("ajustes", client.put(f"/targets/{target_id}/settings", {
                "name": payload["name"],
                "quality_priority": payload["quality_priority"],
                "reload": False})),
            ("filtros", client.put(f"/targets/{target_id}/rules", {
                "rules": payload["rules"], "reload": False})),
            ("mapeos", client.put(f"/targets/{target_id}/mappings", {
                "mappings": payload["mapping_changes"], "reload": False})),
            ("desactivados", client.put(f"/targets/{target_id}/disabled", {
                "disabled_channels": payload["disabled"],
                "reload": False})),
        ]
        for label, (code, body) in steps:
            if code == 0 or code >= 400 or "error" in body:
                return failure(label, code, body)

        # 3) un solo pipeline al final
        code, body = client.run(target_id)
        if code == 0 or code >= 400 or "error" in body:
            return failure("ejecución del pipeline", code, body)
        return {"ok": True, "served": (body or {}).get("filtered_count")}

    def _on_save_finished(self, result: dict) -> None:
        """Aplica el resultado del guardado; corre en el hilo de la GUI."""
        from PyQt6 import sip

        if sip.isdeleted(self):
            return  # el diálogo se cerró mientras se guardaba
        payload = self._save_pending or {}
        self._save_pending = None
        self._saving = False
        if self._save_button is not None:
            self._save_button.setEnabled(True)
        self._config_watch.start()

        if not result.get("ok"):
            self.result_label.setText("")
            self._save_error(result.get("body"), int(result.get("code") or 0),
                             str(result.get("label") or "guardado"))
            return

        # 4) [DAEMON]: se aplica al reiniciar el demonio
        from src.application.services.daemon.config import save_daemon_config

        if payload.get("daemon") is not None and payload.get("client_path"):
            save_daemon_config(Path(payload["client_path"]), payload["daemon"])
        self._config = {**self._config, **self._saved_state(payload)}
        # escritura ajena a la firma del vigilante: no hay que reaccionar
        self._config_signature = self._config_file_signature()
        self._api_ready = False
        self._refresh_mapping()
        self.status_panel.refresh()
        served = result.get("served")
        self.result_label.setText(
            f"Guardado en el demonio: {served if served is not None else '—'} "
            f"canales servidos (puerto {payload.get('port')}).")

    def _mapping_changes(self, current: dict[str, str]) -> list[dict]:
        """Mapeos a enviar: los asignados ahora y los borrados explícitos.

        La API mezcla por ``channel_url``; un ``epg_id`` nulo borra. Sin la
        lista de borrados, quitar una asignación en la tabla no llegaría nunca
        al fichero.
        """
        previous = self._config.get("epg_overrides", {}) or {}
        changes: list[dict] = [{"channel_url": url, "epg_id": cid}
                               for url, cid in current.items()]
        changes += [{"channel_url": url, "epg_id": None}
                    for url in previous if url not in current]
        return changes

    def _save_error(self, body: dict | None, code: int, label: str) -> None:
        """Aviso único de guardado fallido (la UI no escribe a medias)."""
        detail = (body or {}).get("error") or (
            f"HTTP {code}" if code else "el demonio no responde")
        QMessageBox.warning(
            self, "Gestor de listas",
            f"No se pudo guardar ({label}): {detail}. "
            "Arranca el demonio y vuelve a pulsar Guardar.")

    # ------------------------------------------------------------------
    def _with_legacy_fields(self, config: dict) -> dict:
        """Completa los campos heredados de fuente única desde config.ini.

        ``custom_m3u``/``custom_epg``/``epg_source`` ya no se editan en la UI,
        pero se reescriben al guardar: si el llamador no los pasó, se leen del
        fichero para no borrarlos (un target sin fuente dejaría de servir).
        """
        merged = dict(config)
        missing = [k for k in ("custom_m3u", "custom_epg", "epg_source")
                   if k not in merged]
        path = merged.get("_config_path")
        if not missing or path is None:
            return merged
        try:
            from src.infrastructure.client.persistencia import load_config

            targets = load_config(Path(path)).get("targets", {})
            saved = targets.get(int(merged.get("target_id", 0)), {})
        except Exception:  # noqa: BLE001 - config ilegible: se deja como está
            return merged
        for key in missing:
            if key in saved:
                merged[key] = saved[key]
        return merged

    def _catalog_refs(self, widget, kind: str) -> tuple:
        """SourceRefs de las filas activas del widget de cartera."""
        from src.application.services.list_manager.models import SourceRef
        catalog = widget.get_catalog()
        return tuple(
            SourceRef(e.id, e.name, e.location)
            for e in catalog.all() if e.kind == kind and e.enabled
        )

    def _table_loaded(self) -> bool:
        """True si la tabla de mapeo ya tiene filas del pipeline."""
        return self.epg_mapping.table.rowCount() > 0

    def _disabled_now(self) -> tuple[str, ...]:
        """Estado de desactivación efectivo de los canales.

        Antes de poblar la tabla el widget todavía no ha visto ninguna fila:
        hay que usar el valor persistido, o la primera ejecución del pipeline
        serviría canales que el usuario había desactivado.
        """
        if self._reloading or not self._table_loaded():
            return tuple(self._config.get("disabled_channels", ()) or ())
        return self.epg_mapping.get_disabled()

    def _overrides_now(self) -> dict[str, str]:
        """Asignaciones EPG efectivas (url -> channel-id).

        Mismo criterio que ``_disabled_now``: sin tabla cargada se parte de lo
        persistido, para no perder las asignaciones manuales.
        """
        if self._reloading or not self._table_loaded():
            return dict(self._config.get("epg_overrides", {}) or {})
        return self.epg_mapping.get_overrides()

    def _build_filter_preview(self) -> QWidget:
        """Panel derecho de la pestaña «Filtros iptv»: contador + nombres, solo lectura."""
        panel = QGroupBox("Canales que quedan", self.tab_filters)
        lay = QVBoxLayout(panel)
        self.preview_count_label = QLabel("Sin fuente cargada")
        lay.addWidget(self.preview_count_label)
        self.preview_list = QListWidget(panel)
        self.preview_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.preview_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.preview_list)
        return panel

    def _schedule_filter_preview(self) -> None:
        """Reinicia el temporizador de la vista previa (debounce)."""
        self._preview_timer.start()

    def _refresh_filter_preview(self) -> None:
        """Recalcula la vista previa de canales con las reglas actuales."""
        client = self._api_client()
        if client is None:
            return
        rules = [r.serialize() for r in self.rules_widget.get_rules()]
        code, body = client.preview(self._target_id(), rules)
        self.preview_list.clear()
        if code != 200 or "error" in body:
            self.preview_count_label.setText("Demonio no disponible")
            return
        names = list(body.get("channels") or [])
        self.preview_list.addItems(names)
        total = body.get("total") or 0
        self.preview_count_label.setText(
            f"{len(names)} de {total} canales" if total else "Sin fuente cargada")

    def _invalidate_catalog_cache(self) -> None:
        self._groups_cache = None
        self._group_fields_cache = None

    def available_groups(self) -> list[str]:
        """Grupos reales de la fuente, según el daemon (cacheado por diálogo)."""
        if self._groups_cache is None:
            client = self._api_client()
            if client is None:
                return []
            code, body = client.groups(self._target_id())
            if code != 200 or "error" in body:
                return []
            self._groups_cache = list(body.get("groups") or [])
        return list(self._groups_cache)

    def available_group_fields(self) -> dict[str, list[str]]:
        """Campos de agrupación con sus valores, según el daemon (cacheado)."""
        if self._group_fields_cache is None:
            client = self._api_client()
            if client is None:
                return {}
            code, body = client.group_fields(self._target_id())
            if code != 200 or "error" in body:
                return {}
            self._group_fields_cache = dict(body.get("fields") or {})
        return dict(self._group_fields_cache)

    def _refresh_mapping(self) -> None:
        """Repuebla la tabla de mapeo y el resumen con el snapshot del daemon.

        La GUI no ejecuta el pipeline: pide ``mapping-candidates`` (todos los
        canales, o solo los que necesitan atención), ``epg-channels`` (catálogo
        de guía) y el estado del target. El estado editable (asignaciones y desactivados) se captura
        antes de repoblar: leerlo después descartaría lo que el usuario acaba de
        cambiar en las casillas y los desplegables.
        """
        self._invalidate_catalog_cache()
        overrides = self._overrides_now()
        disabled = self._disabled_now()
        client = self._api_client()
        if client is None:
            return
        solo_pendientes = self.epg_mapping.only_pending()
        # Todas las páginas: la tabla no puede perder canales por un límite.
        code, candidates = client.all_mapping_candidates(
            self._target_id(), include_served=not solo_pendientes)
        if code == 0:
            self.epg_mapping.set_epg(None)
            self.epg_mapping.load_unmatched([], overrides)
            self.result_label.setText(
                "Demonio: no responde. Arráncalo para ver los canales.")
            return
        if code != 200:
            self.result_label.setText(
                f"Demonio: {candidates.get('error', f'HTTP {code}')}")
            return
        _, epg = client.all_epg_channels(self._target_id())
        names = {c["id"]: c.get("display_name") or c["id"]
                 for c in epg.get("channels", []) if c.get("id")}
        # Guía(s) que anuncian cada canal: con varias listas el desplegable
        # necesita decir de cuál viene cada id.
        lists = {c["id"]: list(c.get("lists") or [])
                 for c in epg.get("channels", []) if c.get("id")}
        self.epg_mapping.set_epg(
            SimpleNamespace(channel_names=names, epg_lists=lists)
            if names else None)
        rows = [
            SimpleNamespace(
                name=c.get("name", ""),
                url=c["url"],
                logo_url=c.get("logo_url") or "",
                matched_epg_id=c.get("tvg_id") or None,
                disabled=bool(c.get("disabled")),
                served=c.get("served"),
                duplicate=bool(c.get("duplicate")),
            )
            for c in candidates.get("channels", []) if c.get("url")
        ]
        # El daemon sabe qué canales sirve desactivados: se suman a lo que el
        # usuario haya tocado aquí sin guardar.
        disabled = tuple(set(disabled) | {
            c["url"] for c in candidates.get("channels", [])
            if c.get("disabled") and c.get("url")
        })
        self.epg_mapping.load_unmatched(rows, overrides, disabled=disabled)
        self._request_logos()
        _, status = client.target_status(self._target_id())
        served = (status or {}).get("channel_count")
        pending = candidates.get("total") or 0
        parts = []
        if served is not None:
            parts.append(f"{served} canales servidos")
        parts.append(f"{pending} necesitan atención" if pending
                     else "ninguno necesita atención")
        repetidos = sum(1 for c in candidates.get("channels", [])
                        if c.get("duplicate"))
        if repetidos:
            parts.append(f"{repetidos} variantes descartadas por calidad")
        aviso = ""
        if candidates.get("truncated") or epg.get("truncated"):
            aviso = (" — catálogo incompleto: el demonio no devolvió todo "
                     "(faltan canales en las listas)")
        self.result_label.setText("Demonio: " + "; ".join(parts) + "." + aviso)
        self._refresh_filter_preview()

    # ------------------------------------------------------------------
    def _request_logos(self) -> None:
        """Encarga al servicio de logos las urls visibles en la tabla."""
        try:
            for url in self.epg_mapping.logo_urls():
                self._logo_service.request(url)
        except RuntimeError:
            pass  # servicio apagado durante el cierre

    def _on_logo_ready(self, url: str, path: str) -> None:
        """Logo descargado (o servido de caché): píntalo en la tabla."""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        # Escalar a icono de fila (16x16) para no inflar el alto de fila.
        self.epg_mapping.set_logo(url, pixmap.scaled(
            16, 16, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _maybe_stop_daemon(self) -> None:
        """Para el demonio al cerrar, si la casilla está marcada y lo arrancamos.

        Idempotente: Qt llama a ``reject`` (botón Cerrar) o a ``closeEvent``
        (aspa de la ventana) según cómo se cierre.
        """
        if self._daemon_stopped:
            return
        self._daemon_stopped = True
        if not (self._auto_started_daemon
                and self.daemon_panel.stop_on_exit_checked()):
            return
        self.daemon_panel.stop_daemon()

    def closeEvent(self, event) -> None:
        """Cierra el pool de descargas y, si toca, para el demonio."""
        self._maybe_stop_daemon()
        self._config_watch.stop()
        self._preview_timer.stop()
        self._logo_service.shutdown()
        # Un guardado en vuelo no debe retener la ventana: el hilo termina su
        # lote y el resultado se descarta (``_on_save_finished`` comprueba si
        # el diálogo sigue vivo).
        self._save_pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def reject(self) -> None:
        """Botón Cerrar: mismo camino que el aspa de la ventana."""
        self._maybe_stop_daemon()
        self._save_pool.shutdown(wait=False, cancel_futures=True)
        super().reject()

    def _api_client(self) -> ApiClient | None:
        """Cliente REST del daemon (uno por diálogo), o None sin config.ini."""
        if not self._api_ready:
            self._api_ready = True
            from src.application.services.daemon.config import load_daemon_config

            path = self._client_path()
            if path is not None:
                cfg = load_daemon_config(Path(path))
                self._api = ApiClient(host=cfg.control_host,
                                      port=cfg.control_port,
                                      token=cfg.mcp_token)
        return self._api

    def _client_path(self):
        """Fichero del cliente (``weteve.ini``): host, puerto y token.

        Se acepta ``_config_path`` como respaldo para configuraciones de test
        que solo pasan una ruta.
        """
        return self._config.get("_client_path") or self._config.get("_config_path")

    def _target_id(self) -> str:
        return str(self._config.get("target_id", "0"))

    def _toggle_service(self) -> None:
        """Inicia o para el servicio del target EN EL DAEMON.

        Antes el diálogo tenía su propio PlaylistServer en el mismo puerto: dos
        servidores peleando por 9999 y dos pipelines. Ahora la GUI persiste
        puerto/acceso y manda la orden por la API; el contenido lo genera y lo
        sirve el daemon. No hay popup de confirmación: el botón y la URL de la
        propia pestaña muestran el resultado.
        """
        client = self._api_client()
        if client is None:
            return
        code, status = client.target_status(self._target_id())
        if code == 0:
            QMessageBox.warning(
                self, "Gestor de listas",
                "El daemon no responde: arranca el demonio para servir la lista.")
            return
        running = bool(status.get("running"))
        action = "stop" if running else "start"
        code, body = client.service(self._target_id(), action)
        if code != 200 or not body.get("ok"):
            QMessageBox.warning(
                self, "Gestor de listas",
                f"No se pudo {'parar' if running else 'iniciar'} el servicio:\n"
                f"{body.get('error', f'HTTP {code}')}")
            return
        status = body.get("status") or {}
        if status:
            self._apply_service_state(status)
        else:
            self._sync_service_url()
        # El estado ya se ve en la ventana: sin popup de confirmación.
        self.status_panel.refresh()

    def _apply_service_state(self, status: dict) -> None:
        """Pinta botón y URL con un estado ya leído del demonio.

        El botón decía «Iniciar servicio» con el servicio en marcha porque solo
        se refrescaba al pulsarlo: ahora lo pinta cualquier refresco.
        """
        client = self._api_client()
        root = client.root_url if client is not None else ""
        running = bool(status.get("running"))
        self.service_button.setText(
            "&Parar servicio" if running else "&Iniciar servicio")
        if not running:
            self.service_url_label.setText(
                "Servicio parado. Al iniciarlo: "
                f"{root}/weteve/{self._target_id()}/playlist.m3u")
            return
        self.service_url_label.setText(
            f"{status.get('playlist_url')}\n{status.get('epg_url')}")

    def _sync_service_url(self) -> None:
        """Refresca botón y URL con el estado que sirve el demonio.

        El puerto es del demonio, no del target: aquí no se reconfigura nada.
        """
        client = self._api_client()
        if client is None:
            return
        code, status = client.target_status(self._target_id())
        if code != 200:
            self.service_url_label.setText("Demonio: no responde")
            return
        self._apply_service_state(status)

    def get_target_config(self) -> dict:
        """Serializa el estado del diálogo para persistir en config.ini.

        Incluye las selecciones de la cartera (``m3u_refs``/``epg_refs``):
        ``save_config`` las reescribe desde aquí, así que omitirlas borraría
        las fuentes del target en cada guardado.
        """
        return {
            "name": self.name_edit.text().strip(),
            # campos heredados de fuente única: no se editan en la UI, se
            # conservan tal como están en config.ini para no perderlos
            "source": None,
            "source_mode": "custom",
            "custom_m3u": str(self._config.get("custom_m3u") or ""),
            "epg_source": self._config.get("epg_source"),
            "custom_epg": str(self._config.get("custom_epg") or ""),
            "rules": self.rules_widget.get_rules(),
            "epg_overrides": self._overrides_now(),
            "disabled_channels": self._disabled_now(),
            "port": self._config.get("port"),  # legado: ya no se edita
            "bind": self._config.get("bind"),  # legado: ya no se edita
            "m3u_refs": list(
                self._catalog_refs(self.catalog_m3u_widget, "m3u")),
            "epg_refs": list(
                self._catalog_refs(self.catalog_epg_widget, "epg")),
        }
