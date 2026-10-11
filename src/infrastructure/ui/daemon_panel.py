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
from src.infrastructure.client.daemon_launcher import DaemonLauncher

class DaemonPanel(QWidget):
    """Grupo «Demonio»: control del ciclo de vida + estado + aplicar config.

    Arrancar lanza ``daemon.py run`` como proceso desasociado; parar pide
    shutdown por la API de control (parada elegante); reiniciar = stop + start.
    """

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
        self._launcher = DaemonLauncher()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("Demonio: comprobando…")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton("&Iniciar demonio")
        self.btn_stop = QPushButton("&Detener demonio")
        self.btn_restart = QPushButton("&Reiniciar demonio")
        self.btn_apply = QPushButton("A&plicar en demonio")
        self.btn_refresh = QPushButton("Re&frescar")
        for b in (self.btn_start, self.btn_stop, self.btn_restart,
                  self.btn_apply, self.btn_refresh):
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.btn_start.clicked.connect(self.start_daemon)
        self.btn_stop.clicked.connect(self.stop_daemon)
        self.btn_restart.clicked.connect(self.restart_daemon)
        self.btn_apply.clicked.connect(self.apply_to_daemon)
        self.btn_refresh.clicked.connect(self.refresh_status)

    # -- helpers ----------------------------------------------------------------

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            cfg = load_daemon_config(self._config_path)
            self._client = DaemonClient(host=cfg.control_host,
                                        port=cfg.control_port,
                                        token=cfg.mcp_token)
        return self._client

    # -- acciones -----------------------------------------------------------------

    def start_daemon(self) -> None:
        """Arranca el demonio si no responde (proceso desasociado)."""
        client = self._ensure_client()
        if client.ping():
            self.status_label.setText("Demonio: ya está en marcha")
            return
        if self._launcher.launch():
            self.status_label.setText(
                "Demonio: arrancando… (pulsa Refrescar en unos segundos)")
        else:
            self.status_label.setText(
                "Demonio: no se pudo lanzar el proceso (ver logs)")

    def stop_daemon(self) -> None:
        """Parada elegante vía API de control."""
        client = self._ensure_client()
        if client.shutdown():
            self.status_label.setText("Demonio: parando…")
        else:
            self.status_label.setText("Demonio: no responde (ya estaba parado)")

    def restart_daemon(self) -> None:
        """Reiniciar = shutdown + arranque (si estaba vivo y aún se apaga, esperar)."""
        client = self._ensure_client()
        was_alive = client.shutdown()
        if was_alive and client.ping():
            self.status_label.setText(
                "Demonio: reiniciando… (pulsa Refrescar en unos segundos)")
            return
        if self._launcher.launch():
            self.status_label.setText(
                "Demonio: reiniciado, arrancando… (pulsa Refrescar)")
        else:
            self.status_label.setText("Demonio: no se pudo relanzar (ver logs)")

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
                f"Demonio: activo, {len(targets)} target(s); "
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
