"""UI del gestor de listas: wizard 3 pasos + control del servicio web.

Especificación: odd/tasks/list-manager.md. Los diálogos son delgados: delegan
en el pipeline (application) y en el servidor (infrastructure/web).
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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
from src.application.services.list_manager.pipeline import ListManagerPipeline
from src.infrastructure.web.playlist_server import (
    PlaylistServer,
    find_port_owner,
    port_in_use,
)

logger = logging.getLogger(__name__)

FILTER_FIELDS: list[tuple[FilterField, str]] = [
    ("group-title", "Grupo"),
    ("custom-filter", "Nombre/atributos"),
]


class FilterRulesWidget(QWidget):
    """Tabla editable de reglas de filtro simple (modelo xTeVe interno)."""

    def __init__(self, parent=None, on_apply=None, dialog=None):
        super().__init__(parent)
        self._dialog = dialog  # proveedor de grupos (ListManagerDialog)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Campo", "Texto", "Acción", "Mayús./minús."])
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

    def _add_row(self, _checked: bool = False, field: FilterField = "custom-filter",
                 text: str = "", exclude: bool = False,
                 case_sensitive: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        field_combo = QComboBox()
        for value, label in FILTER_FIELDS:
            field_combo.addItem(label, value)
        field_combo.setCurrentIndex(0 if field == "group-title" else 1)
        self.table.setCellWidget(row, 0, field_combo)

        self.table.setItem(row, 1, QTableWidgetItem(text))

        action_combo = QComboBox()
        action_combo.addItem("Incluir", False)
        action_combo.addItem("Excluir", True)
        action_combo.setCurrentIndex(1 if exclude else 0)
        self.table.setCellWidget(row, 2, action_combo)

        cs_check = QCheckBox("Respetar mayús./minús.")
        cs_check.setChecked(case_sensitive)
        self.table.setCellWidget(row, 3, cs_check)

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

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
            self._add_row()
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
        """Pone el grupo en el campo Texto y sugiere tipo "Grupo"."""
        self.table.setItem(row, 1, QTableWidgetItem(group))
        # Si la fila era de tipo Nombre/atributos, cambiar a Grupo:
        # un grupo elegido del listado solo tiene sentido filtrado por grupo.
        combo = self.table.cellWidget(row, 0)
        if combo is not None and combo.currentData() == "custom-filter":
            combo.setCurrentIndex(0)  # "Grupo"

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
                case_sensitive=rule.case_sensitive,
            )

    def get_rules(self) -> list[FilterRule]:
        rules: list[FilterRule] = []
        for row in range(self.table.rowCount()):
            field_combo = self.table.cellWidget(row, 0)
            action_combo = self.table.cellWidget(row, 2)
            cs_check = self.table.cellWidget(row, 3)
            text_item = self.table.item(row, 1)
            text = (text_item.text() if text_item else "").strip()
            if not text:
                continue
            field: FilterField = field_combo.currentData()
            exclude: bool = action_combo.currentData()
            pattern = f"!{{{text}}}" if exclude else text
            rules.append(FilterRule(
                field=field, pattern=pattern, exclude=exclude,
                case_sensitive=cs_check.isChecked(),
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

        # Orígenes (fuente)
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
        self.source_combo.currentIndexChanged.connect(self._refresh_mapping)
        self.epg_combo.currentIndexChanged.connect(self._refresh_mapping)
        layout.addWidget(source_group)

        # Filtro de canales
        filter_group = QGroupBox("Filtro de canales")
        filter_layout = QVBoxLayout(filter_group)
        self.rules_widget = FilterRulesWidget(on_apply=self._refresh_mapping,
                                              dialog=self)
        self.rules_widget.load_rules(config.get("rules", []))
        filter_layout.addWidget(self.rules_widget)
        layout.addWidget(filter_group)

        # Mapeado canal<-->epg
        epg_group = QGroupBox("Mapeado canal<-->epg")
        epg_layout = QVBoxLayout(epg_group)
        self.epg_mapping = EpgMappingWidget()
        epg_layout.addWidget(self.epg_mapping)
        self.result_label = QLabel("")
        epg_layout.addWidget(self.result_label)
        layout.addWidget(epg_group)

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
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # weteve es standalone: el único modo de origen es "custom" (URL o
        # fichero propio). No hay selector de modo ni listas de la app.
        self._update_source_mode()

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

    def _build_target(self) -> PipelineTarget:
        return PipelineTarget(
            name=self.name_edit.text().strip() or "target",
            # La clave de cache es el propio origen: índice (app) o ruta (custom).
            source_index=self._resolve_source_path() or 0,
            rules=tuple(self.rules_widget.get_rules()),
            epg_source_index=self._selected_epg(),
            epg_overrides=self.epg_mapping.get_overrides(),
            port=self.port_spin.value(),
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

    def available_groups(self) -> list[str]:
        """Grupos reales de la fuente actual (para el desplegable de reglas)."""
        source = self._resolve_source_path()
        if source is None:
            return []
        return self._pipeline.get_groups(source)

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
