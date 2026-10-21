"""Panel Qt «Demonio»: estado del daemon y aplicación de la config actual.

UI-only: toda la lógica vive en el daemon (TargetService) y el acceso es vía
DaemonClient (API de control localhost). El panel:
  - muestra si el daemon responde y el estado del target actual,
  - arranca/para/reinicia el demonio y refleja si sirve el target,
  - pide al demonio que recargue el target (no escribe configuración).
"""

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.application.services.daemon.config import DaemonConfig, load_daemon_config
from src.infrastructure.client.daemon_client import DaemonClient
from src.infrastructure.client.daemon_launcher import DaemonLauncher

logger = logging.getLogger(__name__)


class DaemonPanel(QWidget):
    """Grupo «Demonio»: control del ciclo de vida + estado + aplicar config.

    Arrancar lanza ``daemon.py run`` como proceso desasociado; parar pide
    shutdown por la API de control (parada elegante); reiniciar = stop + start.
    """

    def __init__(self, client: DaemonClient | None = None,
                 target_id: str = "0", config_path=None) -> None:
        super().__init__()
        self._client = client
        self._target_id = target_id
        self._config_path = config_path
        self._launcher = DaemonLauncher()
        # último (host, puerto) que respondió: sirve para parar el demonio
        # vivo aunque la config ya apunte a otra dirección
        self._vivo: tuple[str, int] | None = None
        self._cliente_cfg: tuple[str, int, str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("Demonio: comprobando…")
        layout.addWidget(self.status_label)

        # Persistidas en [DAEMON]: la interfaz las aplica al abrir/cerrar.
        saved = (load_daemon_config(self._config_path) if self._config_path
                 else DaemonConfig())
        self.autostart_check = QCheckBox("Arrancar el demonio al abrir")
        self.autostart_check.setChecked(saved.autostart)
        self.autostart_check.setToolTip(
            "Si el demonio no responde al abrir la interfaz, se lanza.")
        self.stop_on_exit_check = QCheckBox(
            "Parar el demonio al cerrar (solo si lo arrancó esta ventana)")
        self.stop_on_exit_check.setChecked(saved.stop_on_exit)
        layout.addWidget(self.autostart_check)
        layout.addWidget(self.stop_on_exit_check)

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

        # Sondeo diferido del arranque: si el diálogo se cierra antes, el
        # temporizador (hijo del panel) muere y no toca widgets destruidos.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_status)

    # -- helpers ----------------------------------------------------------------

    def _ensure_client(self) -> DaemonClient:
        """Cliente de la dirección configurada; se rehace si el fichero cambió."""
        if self._client is not None and self._config_path is None:
            return self._client          # cliente inyectado (tests)
        cfg = (load_daemon_config(self._config_path) if self._config_path
               else DaemonConfig())
        firma = (cfg.control_host, cfg.control_port, cfg.mcp_token)
        if self._client is None or firma != self._cliente_cfg:
            self._client = DaemonClient(host=cfg.control_host,
                                        port=cfg.control_port,
                                        token=cfg.mcp_token)
            self._cliente_cfg = firma
        return self._client

    def _cliente_vivo(self, configurado: DaemonClient) -> DaemonClient:
        """Cliente del demonio que responde, si no es el configurado.

        Tras cambiar el host en el fichero, el demonio sigue escuchando en la
        dirección anterior: para pararlo hay que hablarle ahí.
        """
        if self._vivo is None:
            return configurado
        host, port = self._vivo
        if (host, port) == (configurado.host, configurado.port):
            return configurado
        return DaemonClient(host=host, port=port, token=configurado.token)

    def autostart_checked(self) -> bool:
        return self.autostart_check.isChecked()

    def stop_on_exit_checked(self) -> bool:
        return self.stop_on_exit_check.isChecked()

    def ensure_running(self) -> bool:
        """Arranca el demonio si no responde. True si lo lanzamos nosotros.

        Solo en ese caso la interfaz se considera dueña del proceso y puede
        pararlo al cerrar: un demonio lanzado por el Programador de tareas no
        se toca.
        """
        client = self._ensure_client()
        if client.ping():
            self._vivo = (client.host, client.port)
            return False
        if self._launcher.daemon_address():
            return False
        ok, motivo = self._launcher.start_and_wait(client)
        self.status_label.setText(
            "Demonio: arrancando…" if ok
            else f"Demonio: no arrancó — {motivo}")
        if ok:
            self._vivo = (client.host, client.port)
        return ok

    # -- acciones -----------------------------------------------------------------

    def start_daemon(self) -> None:
        """Arranca el demonio si no responde (proceso desasociado)."""
        client = self._ensure_client()
        if client.ping():
            self.status_label.setText("Demonio: ya está en marcha")
            return
        address = self._launcher.daemon_address()
        if address:
            # el candado lo tiene un demonio vivo en otro puerto: no se lanza otro
            self.status_label.setText(
                f"Demonio: ya está en marcha en {address}")
            return
        ok, motivo = self._launcher.start_and_wait(client)
        if ok:
            self._vivo = (client.host, client.port)
            self.status_label.setText(
                "Demonio: arrancado (pulsa Refrescar para ver el estado)")
        else:
            self.status_label.setText(f"Demonio: no arrancó — {motivo}")

    def esta_vivo(self) -> bool:
        """True si el panel ha hablado con un demonio (hay algo que reiniciar)."""
        return self._vivo is not None

    def config_cambiada(self) -> bool:
        """True si el fichero apunta a otra dirección que la del cliente vivo.

        Guardar un host o un puerto distinto **no** cambia el proceso: hay que
        reiniciarlo para que escuche ahí.
        """
        if self._config_path is None or self._cliente_cfg is None:
            return False
        cfg = load_daemon_config(self._config_path)
        return (cfg.control_host, cfg.control_port, cfg.mcp_token) != self._cliente_cfg

    def stop_daemon(self) -> None:
        """Parada elegante vía API de control."""
        client = self._ensure_client()
        if client.shutdown():
            self.status_label.setText("Demonio: parando…")
            return
        # Sin respuesta puede ser que el token haya cambiado: el demonio sigue
        # vivo y con el candado. Se para por PID.
        if self._launcher.stop_running():
            self._vivo = None
            self.status_label.setText("Demonio: parado (no aceptaba órdenes)")
        else:
            self.status_label.setText("Demonio: no responde (ya estaba parado)")

    def restart_daemon(self) -> None:
        """Reiniciar = shutdown + arranque (si estaba vivo y aún se apaga, esperar)."""
        client = self._ensure_client()
        # si la config apunta a otra dirección, el demonio vivo sigue en la
        # anterior: se para hablándole ahí
        fue_vivo = self._cliente_vivo(client).shutdown()
        if fue_vivo and client.ping():
            self.status_label.setText(
                "Demonio: reiniciando… (pulsa Refrescar en unos segundos)")
            return
        if not fue_vivo and not self._launcher.stop_running():
            self.status_label.setText(
                "Demonio: no se pudo parar el que estaba en marcha")
            return
        ok, motivo = self._launcher.start_and_wait(client)
        if ok:
            self._vivo = (client.host, client.port)
            self.status_label.setText("Demonio: reiniciado")
        else:
            self.status_label.setText(f"Demonio: no arrancó — {motivo}")

    def refresh_later(self) -> None:
        """Programa un refresh para cuando la ventana ya esté visible."""
        self._refresh_timer.start(0)

    def refresh_status(self) -> None:
        """Pinta el estado del demonio (puede llegar mientras se cierra)."""
        try:
            self._refresh_status()
        except RuntimeError:
            # el temporizador disparó entre la destrucción del panel y la del
            # propio timer: no hay nada que pintar
            logger.debug("panel del demonio destruido durante el refresco")

    def _refresh_status(self) -> None:
        from PyQt6 import sip

        if sip.isdeleted(self) or sip.isdeleted(self.status_label):
            return
        client = self._ensure_client()
        status = client.status()
        if status is None:
            self.status_label.setText(
                "Demonio: no responde (arranca con «python daemon.py run» "
                "o registra la tarea con «python daemon.py install-task»)")
            return
        self._vivo = (client.host, client.port)
        targets = status.get("targets", {})
        t = targets.get(self._target_id)
        if t is None:
            self.status_label.setText(
                f"Demonio: activo, {len(targets)} perfil(es); "
                f"este perfil ({self._target_id}) no está en marcha")
            return
        state = ("sirviendo" if t.get("running") else "parado")
        extra = (f", {t.get('channel_count', 0)} canales"
                 if t.get("running") else "")
        err = f" — ERROR: {t['error']}" if t.get("error") else ""
        self.status_label.setText(
            f"Demonio: activo — «{t.get('name', '?')}» {state}{extra}{err}")

    def apply_to_daemon(self) -> None:
        """Pide al demonio que recargue el target.

        No escribe nada: el fichero lo escribe la API del demonio (Guardar).
        Antes guardaba aquí con ``TargetStore``, y eso era un segundo escritor.
        """
        client = self._ensure_client()
        if client.reload_target(self._target_id):
            self.status_label.setText("Demonio: config aplicada y perfil recargado")
        else:
            self.status_label.setText(
                "Demonio: el daemon no responde: recargará al arrancar")
