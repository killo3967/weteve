"""Widget Qt de cartera de fuentes: tabla editable Nombre/Tipo/Ubicación/Refresco/Activa.

Se instancia dos veces en el diálogo (pestañas Listas IPTV y Listas EPG);
cada una solo muestra las filas de su tipo (m3u / epg).
"""

import logging

from PyQt6.QtCore import QDir, QObject, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import pyqtSignal

from src.application.services.list_manager.source_catalog import (
    SourceCatalog,
    new_source_id,
)

logger = logging.getLogger(__name__)

_COLS = ("Nombre", "Tipo de origen", "URL o fichero", "Refresco (min)", "Activa")
_TYPE_LABELS = {"file": "Fichero local", "url": "URL remota"}


class _EditorSignals(QObject):
    commit = pyqtSignal()


class LocationDelegate(QStyledItemDelegate):
    """Celda de ubicación: QLineEdit + botón «…» para ficheros locales.

    La detección URL/fichero vive aquí (infer_source_type): si el texto
    empieza por http(s) es URL remota y el botón se deshabilita; en caso
    contrario abre un QFileDialog con el filtro del tipo de pestaña.
    """

    # un EPG real es XML (a veces .mlx) y suele venir comprimido en .gz
    # (xml dentro); el repositorio ya descomprime por extensión o magic
    FILTER_M3U = "Listas M3U (*.m3u *.m3u8);;Todos los ficheros (*)"
    FILTER_EPG = (
        "Guías XMLTV (*.xml *.xmltv *.mlx *.gz);;"
        "Todos los ficheros (*)"
    )

    def __init__(self, kind: str = "m3u", parent=None) -> None:
        super().__init__(parent)
        self._kind = kind

    @property
    def FILTER(self) -> str:  # noqa: N802 - compat con tests existentes
        return self.FILTER_M3U if self._kind == "m3u" else self.FILTER_EPG

    @staticmethod
    def infer_source_type(location: str) -> str:
        loc = (location or "").strip().lower()
        return "url" if loc.startswith(("http://", "https://")) else "file"

    def createEditor(self, parent, option, index):
        editor = QWidget(parent)
        lay = QHBoxLayout(editor)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        line = QLineEdit(editor)
        btn = QPushButton("…", editor)
        btn.setFixedWidth(24)
        lay.addWidget(line, 1)
        lay.addWidget(btn)
        editor._line = line
        editor._btn = btn
        editor._signals = _EditorSignals(editor)
        line.textChanged.connect(lambda t: btn.setEnabled(
            not (t or "").strip().lower().startswith(("http://", "https://"))))
        line.editingFinished.connect(editor._signals.commit)

        def _browse():
            start = line.text().strip()
            path, _ = QFileDialog.getOpenFileName(
                editor, "Seleccionar fichero", start, self.FILTER)
            if path:
                line.setText(QDir.toNativeSeparators(path))
                editor._signals.commit.emit()

        btn.clicked.connect(_browse)
        return editor

    def setEditorData(self, editor, index):
        editor._line.setText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor._line.text())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _TypeDelegate(QStyledItemDelegate):
    """Celda «Tipo de origen»: combo Fichero local / URL remota.

    Elegir URL remota con la ubicación vacía o de ruta local la convierte
    en http:// para completar; elegir Fichero local solo tiene efecto si
    la ubicación no es una URL http(s) (una URL no se puede «convertir»).
    """

    OPTIONS = ("Fichero local", "URL remota")

    def createEditor(self, parent, option, index):
        from PyQt6.QtWidgets import QComboBox
        # QComboBox nativo como editor directo: flecha y popup garantizados
        editor = QComboBox(parent)
        editor.addItems(self.OPTIONS)
        editor.currentIndexChanged.connect(self._on_activated)
        return editor

    def _on_activated(self, _index: int) -> None:
        # el usuario eligió (popup o programático): comprometer y cerrar
        self.commitData.emit(self.sender())
        self.closeEditor.emit(self.sender())

    def setEditorData(self, editor, index):
        current = index.data() or self.OPTIONS[0]
        i = self.OPTIONS.index(current) if current in self.OPTIONS else 0
        editor.blockSignals(True)
        editor.setCurrentIndex(i)
        editor.blockSignals(False)

    def setModelData(self, editor, model, index):
        want = editor.currentText()
        loc_model = index.sibling(index.row(), 2)
        loc = (loc_model.data() or "").strip()
        is_url = LocationDelegate.infer_source_type(loc) == "url"
        if want == "URL remota" and not is_url:
            model.setData(loc_model, "http://")
            model.setData(index, "URL remota")
        elif want == "Fichero local" and not is_url:
            model.setData(index, "Fichero local")
        # si es http y piden fichero: la ubicación manda, no se cambia

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class SourceCatalogWidget(QWidget):
    """Tabla de filas de la cartera filtrada por ``kind`` (m3u/epg)."""

    def __init__(self, kind: str, catalog: SourceCatalog | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        if kind not in ("m3u", "epg"):
            raise ValueError(f"kind inválido: {kind}")
        self._kind = kind
        self._catalog = catalog or SourceCatalog()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(_COLS), self)
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.setItemDelegateForColumn(
            2, LocationDelegate(self._kind, self.table))
        self.table.setItemDelegateForColumn(
            1, _TypeDelegate(self.table))
        # sincronizar tipo mostrado cuando la ubicación cambia a mano
        self.table.itemChanged.connect(self._sync_type_label)
        # un solo clic sobre la celda de tipo abre el desplegable
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        add = QPushButton("&Añadir")
        add.clicked.connect(self.add_row)
        remove = QPushButton("&Quitar")
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.reload()

    # -- datos ----------------------------------------------------------------

    def reload(self) -> None:
        """Repuebla la tabla desde la cartera (filas de este tipo)."""
        entries = [e for e in self._catalog.all() if e.kind == self._kind]
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._fill_row(row, e)

    def _fill_row(self, row: int, e) -> None:
        self.table.setItem(row, 0, QTableWidgetItem(e.name))
        tipo = QTableWidgetItem(_TYPE_LABELS.get(e.source_type, e.source_type))
        self.table.setItem(row, 1, tipo)
        self.table.setItem(row, 2, QTableWidgetItem(e.location))
        self.table.setItem(row, 3, QTableWidgetItem(str(e.refresh_min)))
        active = QTableWidgetItem()
        active.setFlags(Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsUserCheckable)
        active.setCheckState(Qt.CheckState.Checked if e.enabled
                             else Qt.CheckState.Unchecked)
        self.table.setItem(row, 4, active)
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, e.id)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == 1:
            self._open_type_editor(row, column)

    def _open_type_editor(self, row: int, column: int) -> None:
        """Abre el desplegable de tipo con un solo clic."""
        from PyQt6.QtWidgets import QAbstractItemView, QStyledItemDelegate
        # cerrar cualquier editor activo (p. ej. el de la ubicación que
        # add_row deja abierto): hay que commitar vía las señales del
        # delegado de la celda en edición (currentColumn() es -1 si el
        # click no pasó por setCurrentCell); fallback: delegado default
        if self.table.state() == QAbstractItemView.State.EditingState:
            editor = self.table.focusWidget()
            col = self.table.currentColumn()
            if col < 0:
                col = column
            delegate = self.table.itemDelegateForColumn(col)
            delegate.commitData.emit(editor)
            delegate.closeEditor.emit(
                editor, QStyledItemDelegate.EndEditHint.NoHint)
        self.table.editItem(self.table.item(row, column))
        # popup inmediato: elegir sin clicks extra
        ed = self.table.focusWidget()
        if isinstance(ed, QComboBox):
            ed.showPopup()

    def _sync_type_label(self, item) -> None:
        """Etiqueta de tipo al día cuando la ubicación se edita a mano."""
        col = item.column()
        row = item.row()
        if col == 2:
            tipo = self.table.item(row, 1)
            if tipo is not None:
                tipo.setText(_TYPE_LABELS[LocationDelegate.infer_source_type(
                    (item.text() or "").strip())])

    def add_row(self) -> None:
        """Fila nueva: fichero local, refresco 0 (estática), activa."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        entry = type(self._catalog.all()[0]) if self._catalog.all() else None
        from src.application.services.list_manager.source_catalog import SourceEntry
        e = SourceEntry(new_source_id(), "", self._kind, "file", "", 0, True)
        self._fill_row(row, e)
        self.table.editItem(self.table.item(row, 2))

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def get_catalog(self) -> SourceCatalog:
        """Cartera reconstituida desde la tabla (otros tipos intactos)."""
        from src.application.services.list_manager.source_catalog import SourceEntry
        entries = [e for e in self._catalog.all() if e.kind != self._kind]
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            if name_item is None:
                continue
            eid = name_item.data(Qt.ItemDataRole.UserRole) or new_source_id()
            location = self.table.item(row, 2).text().strip()
            try:
                refresh = max(0, int(self.table.item(row, 3).text().strip() or 0))
            except ValueError:
                refresh = 0
            entries.append(SourceEntry(
                id=eid,
                name=name_item.text().strip(),
                kind=self._kind,
                source_type=LocationDelegate.infer_source_type(location),
                location=location,
                refresh_min=refresh,
                enabled=self.table.item(row, 4).checkState()
                == Qt.CheckState.Checked,
            ))
        return SourceCatalog(entries)

    # -- selección del target ---------------------------------------------------

    def selected_ids(self) -> list[str]:
        """Ids de las filas activas (selección del target para ese tipo)."""
        out = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            active = self.table.item(row, 4)
            if active and active.checkState() == Qt.CheckState.Checked:
                out.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def set_selection(self, ids: list[str]) -> None:
        """Marca Activa solo en las filas cuyos id estén en ``ids``."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            active = self.table.item(row, 4)
            if active:
                active.setCheckState(
                    Qt.CheckState.Checked
                    if str(item.data(Qt.ItemDataRole.UserRole)) in ids
                    else Qt.CheckState.Unchecked
                )
