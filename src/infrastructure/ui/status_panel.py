"""Pestaña «Estado»: semáforo del demonio y URLs de acceso de cada servicio.

Todo se sirve en el mismo puerto del demonio, así que la pestaña enseña una
única raíz y, debajo, la ruta de cada servicio (control, REST, MCP, playlist y
EPG) más el estado de cada target.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

GREEN = "\U0001F7E2"   # 🟢
RED = "\U0001F534"     # 🔴

DEFAULT_ROOT = "http://127.0.0.1:34400"


class StatusPanel(QWidget):
    """Vista de solo lectura del demonio: responde o no, y dónde se accede."""

    def __init__(self, client_factory, target_id: str = "0", parent=None) -> None:
        super().__init__(parent)
        self._client_factory = client_factory
        self._target_id = str(target_id)

        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        self.dot = QLabel(RED)
        font = self.dot.font()
        font.setPointSize(22)
        self.dot.setFont(font)
        self.state_label = QLabel("Comprobando…")
        head.addWidget(self.dot)
        head.addWidget(self.state_label, 1)
        self.refresh_button = QPushButton("Co&mprobar de nuevo")
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button)
        layout.addLayout(head)

        self.services_group = QGroupBox("URLs de acceso (un solo puerto)")
        self.services_form = QFormLayout(self.services_group)
        layout.addWidget(self.services_group)

        self.targets_group = QGroupBox("Targets")
        self.targets_layout = QVBoxLayout(self.targets_group)
        layout.addWidget(self.targets_group)

        layout.addStretch(1)
        self.refresh()

    # -- pintado -------------------------------------------------------------

    def _set_state(self, emoji: str, text: str) -> None:
        self.dot.setText(emoji)
        self.state_label.setText(text)

    def _url_rows(self, root: str) -> list[tuple[str, str]]:
        return [
            ("API de control", f"{root}/health"),
            ("REST", f"{root}/api/v1"),
            ("MCP", f"{root}/mcp"),
            ("Playlist", f"{root}/m3u/weteve_{self._target_id}.m3u"),
            ("EPG", f"{root}/xmltv/weteve_{self._target_id}.xml"),
        ]

    def _fill_urls(self, root: str) -> None:
        while self.services_form.rowCount():
            self.services_form.removeRow(0)
        for label, url in self._url_rows(root):
            value = QLabel(url)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self.services_form.addRow(f"{label}:", value)

    def _clear_targets(self) -> None:
        while self.targets_layout.count():
            item = self.targets_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_target_line(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.targets_layout.addWidget(label)

    # -- datos ---------------------------------------------------------------

    def refresh(self) -> None:
        """Consulta el demonio y repinta semáforo, URLs y targets."""
        client = self._client_factory()
        if client is None:
            self._fill_urls(DEFAULT_ROOT)
            self._clear_targets()
            self._add_target_line("Sin config.ini: no hay demonio que consultar.")
            self._set_state(RED, "Demonio: sin configuración")
            return

        code, status = client.status()
        self._fill_urls(client.root_url)
        self._clear_targets()

        if code != 200:
            self._set_state(RED, f"Demonio parado: no responde en {client.root_url}")
            self._add_target_line(
                "Arranca el demonio (panel «Demonio» en Configuración) para "
                "ver su estado")
            return

        targets = status.get("targets") or {}
        served = [t for t in targets.values() if t.get("running")]
        self._set_state(
            GREEN,
            f"Demonio en marcha en {client.root_url} · "
            f"{len(served)} de {len(targets)} perfil(es) sirviendo")

        if not targets:
            self._add_target_line("Sin perfiles definidos en daemon.toml.")
        for tid, t in sorted(targets.items()):
            running = bool(t.get("running"))
            line = (f"{GREEN if running else RED} Perfil {tid} · "
                    f"{t.get('name', '?')} · "
                    f"{'sirviendo' if running else 'parado'}")
            if running and t.get("channel_count") is not None:
                line += f" · {t.get('channel_count')} canales"
            if t.get("error"):
                line += f" · ERROR: {t['error']}"
            self._add_target_line(line)
            if running and t.get("playlist_url"):
                self._add_target_line(
                    f"    {t['playlist_url']}\n    {t.get('epg_url') or ''}")
