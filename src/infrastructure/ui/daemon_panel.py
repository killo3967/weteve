"""Panel Qt «Demonio»: estado del daemon y aplicación de la config actual.

UI-only: toda la lógica vive en el daemon (TargetSupervisor) y el acceso es
vía DaemonClient (API de control localhost). El panel:
  - muestra si el daemon responde y el estado del target actual,
  - guarda la configuración del diálogo en config.ini (TargetStore),
  - pide reload del target al daemon si está vivo.
"""

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.application.services.daemon.config import load_daemon_config
from src.application.services.daemon.target_store import TargetStore
from src.infrastructure.client.daemon_client import DaemonClient

class DaemonPanel(QWidget):
    """Grupo «Demonio» dentro del diálogo del gestor de listas."""

    def __init__(self, client: DaemonClient | None = None,
                 target_store: TargetStore | None = None,
                 target_id: str = "0",
                 get_target_config=None, config_path=None) -> None:
        super().__init__()
        self._client = client
        self._store = target_store
        self._target_id = target_id
        self._get_target_config = get_target_config or (lambda: {})
        self._config_path = config_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("Demonio: comprobando…")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.btn_refresh = QPushButton("&Refrescar estado")
        self.btn_apply = QPushButton("&Aplicar en demonio")
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_apply)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.btn_refresh.clicked.connect(self.refresh_status)
        self.btn_apply.clicked.connect(self.apply_to_daemon)

    # -- helpers ----------------------------------------------------------------

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            cfg = load_daemon_config(self._config_path)
            self._client = DaemonClient(host=cfg.control_host,
                                        port=cfg.control_port,
                                        token=cfg.mcp_token)
        return self._client

    # -- acciones -----------------------------------------------------------------

    def refresh_status(self) -> None:
        client = self._ensure_client()
        status = client.status()
        if status is None:
            self.status_label.setText(
                "Demonio: no responde (arranca con «python daemon.py run» "
                "o registra la tarea con «python daemon.py install-task»)")
            return
        targets = status.get("targets", {})
        t = targets.get(self._target_id)
        if t is None:
            self.status_label.setText(
                f"Daemonio: activo, {len(targets)} target(s); "
                f"este target ({self._target_id}) no está en marcha")
            return
        state = ("sirviendo" if t.get("running") else "parado")
        extra = (f", {t.get('channel_count', 0)} canales"
                 if t.get("running") else "")
        err = f" — ERROR: {t['error']}" if t.get("error") else ""
        self.status_label.setText(
            f"Demonio: activo — «{t.get('name', '?')}» {state}{extra}{err}")

    def apply_to_daemon(self) -> None:
        """Guarda la config actual del diálogo y pide reload al daemon."""
        target = self._get_target_config()
        if target is None:
            self.status_label.setText("Demonio: no hay configuración que aplicar")
            return
        if self._store is not None:
            self._store.save(self._target_id, target)
        client = self._ensure_client()
        if client.reload_target(self._target_id):
            self.status_label.setText("Demonio: config aplicada y target recargado")
        else:
            self.status_label.setText(
                "Demonio: config guardada; el daemon no responde — "
                "recargará al arrancar")
