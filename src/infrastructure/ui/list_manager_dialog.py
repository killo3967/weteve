"""UI del gestor de listas: wizard 3 pasos + control del servicio web.

Especificación: odd/tasks/list-manager.md. Los diálogos son delgados: delegan
en el pipeline (application) y en el servidor (infrastructure/web).
"""

import logging
import re
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
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


from src.application.services.list_manager.dummy_epg import (
    BLANK_LABEL,
    DUMMY_LABEL,
    is_dummy_id,
    is_dummy_label,
)
from src.application.services.list_manager.models import (
    FilterField,
    FilterRule,
    PipelineTarget,
)
from src.application.services.list_manager.playlist_builder import build_dummy_epg_id
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
            "Canales sin guía EPG real. Elige el canal XMLTV en 'EPG asignado', o "
            "'Dummy 1h' para servirlos con una guía de relleno de 1 h. Sin "
            "asignación quedan fuera de la lista; desmarca 'Activo' para sacarlos."
        )
        layout.addWidget(self.info_label)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Canal M3U", "(url)", "Logo", "EPG asignado", "Activo"]
        )
        self.table.setColumnHidden(self.COL_URL, True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(self.COL_EPG, 180)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_EPG, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_LOGO, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(self.COL_LOGO, 40)
        self.table.horizontalHeader().setSectionResizeMode(
            self.COL_ACTIVE, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self._epg_names: dict[str, str] = {}
        # True si hay fuente EPG: es lo que activa la regla de servidor
        # ('sin guía no se sirve'). Sin fuente, la casilla 'Activo' sigue
        # siendo solo una decisión del usuario.
        self._require_epg = False
        # urls de la última carga: 'Dummy 1h' necesita la url para derivar su id
        # sintético, y la casilla 'Activo' se recalea al cambiar el desplegable.
        self._row_urls: list[str] = []
        # urls apagadas a mano (las que se persisten en disabled_channels)
        self._manual_off: set[str] = set()
        # mientras se repuebla la tabla se mueven casillas por programa: hay que
        # ignorar esos itemChanged, o convertirían un estado derivado en una
        # decisión del usuario.
        self._loading = False
        self.table.itemChanged.connect(self._on_item_changed)

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

    def epg_channel_names(self) -> list[str]:
        """Opciones del desplegable: 'Dummy 1h' primero y luego la guía.

        Se listan la etiqueta limpia y el channel-id: en TV.xml ambos coinciden
        ('La1'), pero otras fuentes usan ids distintos del nombre ('es.la1') y
        poder pegar el id sigue siendo útil.
        """
        out: list[str] = []
        for channel_id, display in self._epg_names.items():
            out.append(self._clean(display))
            if channel_id != display:
                out.append(channel_id)
        names = sorted({x for x in out if x})
        return ([DUMMY_LABEL] + names) if self._dummy_offered() else names

    def set_epg(self, epg) -> None:
        """Carga el catálogo XMLTV que ofrecerán los desplegables de la tabla."""
        self._epg_names = dict(epg.channel_names) if epg is not None else {}
        self._require_epg = epg is not None

    def _resolve_epg_id(self, value: str) -> str:
        """Nombre visible (con o sin sufijo) o id del EPG -> channel-id real.

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
        for channel_id, display in self._epg_names.items():
            if value in (display, self._clean(display)):
                return channel_id
        return value

    def _display_label(self, channel_id: str) -> str:
        """Canal-id persistido -> etiqueta legible para el desplegable."""
        if is_dummy_id(channel_id):
            return DUMMY_LABEL
        display = self._epg_names.get(channel_id)
        return self._clean(display) if display else channel_id

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
            self._row_urls = [ch.url for ch in channels]
            names = self.epg_channel_names()
            self.table.setRowCount(0)
            self.table.setRowCount(len(channels))
            for row, ch in enumerate(channels):
                name_item = QTableWidgetItem(ch.name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, self.COL_NAME, name_item)

                url_item = QTableWidgetItem(ch.url)
                self.table.setItem(row, self.COL_URL, url_item)

                # Logo: placeholder; el servicio de logos rellena la celda async.
                logo_item = QTableWidgetItem()
                logo_item.setData(Qt.ItemDataRole.UserRole, ch.logo_url or "")
                logo_item.setFlags(logo_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, self.COL_LOGO, logo_item)

                assigned = overrides.get(ch.url, "")
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
        return True if not self._require_epg else has_assignment

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
        """
        overrides: dict[str, str] = {}
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
            if cid:
                overrides[url_item.text()] = cid
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

        # Descarga asíncrona de logos con caché en disco (TTL 7 días).
        from src.infrastructure.ui.logo_cache_service import LogoCacheService
        self._logo_service = LogoCacheService(
            Path(__file__).resolve().parents[2] / "cache" / "logos", self)
        self._logo_service.logo_ready.connect(self._on_logo_ready)

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

        # Pestaña «Configuración»: target + servicio web + demonio ([DAEMON]).
        self.tab_config = QWidget(self)
        config_tab_layout = QVBoxLayout(self.tab_config)
        config_tab_layout.addWidget(service_group)

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
        daemon_group = QGroupBox("Demonio (API de control y MCP)")
        daemon_form = QFormLayout(daemon_group)
        self.control_host_edit = QLineEdit(self._daemon_cfg.control_host)
        daemon_form.addRow("&Host de control:", self.control_host_edit)
        self.control_port_spin = QSpinBox()
        self.control_port_spin.setRange(1024, 65535)
        self.control_port_spin.setValue(self._daemon_cfg.control_port)
        daemon_form.addRow("Puerto de &control:", self.control_port_spin)
        self.mcp_port_spin = QSpinBox()
        self.mcp_port_spin.setRange(1024, 65535)
        self.mcp_port_spin.setValue(self._daemon_cfg.mcp_port)
        daemon_form.addRow("Puerto &MCP:", self.mcp_port_spin)
        self.mcp_token_edit = QLineEdit(self._daemon_cfg.mcp_token)
        self.mcp_token_edit.setPlaceholderText("Vacío = sin autenticación (solo localhost)")
        daemon_form.addRow("&Token MCP:", self.mcp_token_edit)
        config_tab_layout.addWidget(daemon_group)
        config_tab_layout.addStretch()

        # Demonio: estado y aplicación de la config al servicio permanente
        from src.application.services.daemon.target_store import TargetStore
        from src.infrastructure.ui.daemon_panel import DaemonPanel
        config_path = config.get("_config_path")
        store = TargetStore(config_path) if config_path else None
        self.daemon_panel = DaemonPanel(
            target_store=store, target_id=str(config.get("target_id", "0")),
            get_target_config=self._build_target, config_path=config_path)
        self.daemon_panel.refresh_status()
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
        save_config(Path(config_path), {"targets": {0: new_config}})
        # 3) sección [DAEMON] (pestaña Configuración)
        from src.application.services.daemon.config import (
            DaemonConfig,
            save_daemon_config,
        )
        save_daemon_config(Path(config_path), DaemonConfig(
            control_host=self.control_host_edit.text().strip() or "127.0.0.1",
            control_port=self.control_port_spin.value(),
            mcp_port=self.mcp_port_spin.value(),
            mcp_token=self.mcp_token_edit.text().strip(),
        ))
        # El daemon también sirve este target; si está vivo hay que avisarle,
        # si no seguirá con los filtros que leyó al arrancar. El aviso es
        # best-effort y bloqueante (ping HTTP hasta 5 s): va en segundo plano
        # para no congelar la UI cuando el demonio no responde.
        from PyQt6.QtCore import QThreadPool
        from src.infrastructure.ui.daemon_panel import NotifyDaemonTask
        port = new_config.get("port")
        name = new_config["name"]
        task = NotifyDaemonTask(self.daemon_panel)
        task.signals.done.connect(lambda ok: self.result_label.setText(
            f"Configuración guardada: {name} "
            f"(puerto {port})."
            + (" Demonio avisado: recargando."
               if ok else " No se notificó al demonio (no responde).")))
        QThreadPool.globalInstance().start(task)

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

    def _pipeline_cache_key(self) -> int | str:
        """Clave con la que el pipeline cachea fuente y EPG de este target.

        Debe coincidir exactamente con ``PipelineTarget.source_index``: en modo
        cartera la unión se registra bajo ``"merged"``; si no, es la ruta/índice
        del origen. Leer la EPG con otra clave devuelve None y deja la tabla de
        mapeo sin nombres de canal.
        """
        if self._catalog_refs(self.catalog_m3u_widget, "m3u"):
            return "merged"
        return self._resolve_source_path() or 0

    def _table_loaded(self) -> bool:
        """True si la tabla de mapeo ya tiene filas del pipeline."""
        return self.epg_mapping.table.rowCount() > 0

    def _disabled_now(self) -> tuple[str, ...]:
        """Estado de desactivación efectivo de los canales.

        Antes de poblar la tabla el widget todavía no ha visto ninguna fila:
        hay que usar el valor persistido, o la primera ejecución del pipeline
        serviría canales que el usuario había desactivado.
        """
        if not self._table_loaded():
            return tuple(self._config.get("disabled_channels", ()) or ())
        return self.epg_mapping.get_disabled()

    def _overrides_now(self) -> dict[str, str]:
        """Asignaciones EPG efectivas (url -> channel-id).

        Mismo criterio que ``_disabled_now``: sin tabla cargada se parte de lo
        persistido, para no perder las asignaciones manuales.
        """
        if not self._table_loaded():
            return dict(self._config.get("epg_overrides", {}) or {})
        return self.epg_mapping.get_overrides()

    def _build_target(self) -> PipelineTarget:
        # multi-fuente: hay filas activas en la cartera => unión;
        # si no, flujo legacy de fuente única (custom_m3u/custom_epg)
        m3u_refs = self._catalog_refs(self.catalog_m3u_widget, "m3u")
        epg_refs = self._catalog_refs(self.catalog_epg_widget, "epg")
        return PipelineTarget(
            name=self.name_edit.text().strip() or "target",
            # La clave de cache es el propio origen: índice (app) o ruta (custom).
            source_index=self._pipeline_cache_key(),
            rules=tuple(self.rules_widget.get_rules()),
            epg_source_index=self._selected_epg(),
            epg_overrides=self._overrides_now(),
            disabled_channels=self._disabled_now(),
            port=self.port_spin.value(),
            source_refs=m3u_refs,
            epg_refs=epg_refs,
        )

    def _run_pipeline(self):
        target = self._build_target()
        if target.source_refs:
            # multi-fuente: la unión se registra bajo la clave "merged";
            # no hay custom_m3u que resolver en este modo
            self._pipeline.register_merged(
                target.source_index, target.source_refs, target.epg_refs)
            try:
                return self._pipeline.run(target)
            except Exception as e:
                logger.error("Error ejecutando pipeline: %s", e)
                QMessageBox.warning(
                    self, "Gestor de listas", f"Error al procesar la lista:\n{e}")
                return None
        cache_key = self._resolve_source_path()
        m3u_path = self._source_m3u_path()
        if cache_key is None or m3u_path is None:
            return None
        epg_path = self._resolve_epg_path()
        self._pipeline.register_source(cache_key, m3u_path, epg_path)
        try:
            return self._pipeline.run(target)
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
            "Guías XMLTV (*.xml *.xmltv *.mlx *.gz);;Todos los ficheros (*)",
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
        """Ejecuta el pipeline y actualiza el editor de EPG + resumen.

        El estado editable (asignaciones y desactivados) se captura antes de
        repoblar la tabla: leerlo de ``_config`` después descartaría lo que el
        usuario acaba de cambiar en las casillas y los desplegables.
        """
        overrides = self._overrides_now()
        disabled = self._disabled_now()
        result = self._run_pipeline()
        if result is None:
            return
        epg = self._pipeline.get_epg(self._pipeline_cache_key())
        self.epg_mapping.set_epg(epg)
        # Filas = canales que necesitan atención. Con fuente EPG los servidos
        # traen todos guía, así que los que caen sin asignar viven en
        # ``excluded_no_epg``: si no se listaran, el usuario no vería qué
        # canales ha descartado el servidor ni podría rescatarlos. Los
        # desactivados a mano también van: es la única forma de reactivarlos.
        rows: list = []
        seen: set[str] = set()
        for ch in (result.excluded_no_epg + result.excluded_disabled
                   + [c for c in result.channels if c.matched_epg_id is None]):
            if ch.url in seen:
                continue
            seen.add(ch.url)
            rows.append(ch)
        self.epg_mapping.load_unmatched(rows, overrides, disabled=disabled)
        self._request_logos()
        summary = (
            f"{result.total_input} canales en la fuente → "
            f"{result.filtered_count} en la lista "
            f"({len(result.excluded_disabled)} desactivados)"
        )
        # La regla de servidor solo descarta si hay fuente EPG; sin ella
        # anunciar descartes sería inventarse un problema.
        if result.require_epg and result.excluded_no_epg:
            summary += (
                f" → {len(result.excluded_no_epg)} sin guía EPG, descartados; "
                "asígnales un canal o 'Dummy 1h'."
            )
        else:
            summary += f" → {len(result.unmatched)} sin guía EPG real."
        self.result_label.setText(summary)

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

    def closeEvent(self, event) -> None:
        """Apaga el pool de descargas al cerrar el diálogo."""
        self._logo_service.shutdown()
        super().closeEvent(event)

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

        epg = self._pipeline.get_epg(self._pipeline_cache_key())
        playlist = build_m3u(result.channels)
        epg_xml = build_xmltv(result.channels, epg)
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
        """Serializa el estado del diálogo para persistir en config.ini.

        Incluye las selecciones de la cartera (``m3u_refs``/``epg_refs``):
        ``save_config`` las reescribe desde aquí, así que omitirlas borraría
        las fuentes del target en cada guardado.
        """
        return {
            "name": self.name_edit.text().strip(),
            "source": self._selected_source(),
            "source_mode": self._source_mode(),
            "custom_m3u": self.custom_m3u_edit.text().strip(),
            "epg_source": self._selected_epg(),
            "custom_epg": self.custom_epg_edit.text().strip(),
            "rules": self.rules_widget.get_rules(),
            "epg_overrides": self._overrides_now(),
            "disabled_channels": self._disabled_now(),
            "port": self.port_spin.value(),
            "bind": self.bind_combo.currentData(),
            "m3u_refs": list(
                self._catalog_refs(self.catalog_m3u_widget, "m3u")),
            "epg_refs": list(
                self._catalog_refs(self.catalog_epg_widget, "epg")),
        }
