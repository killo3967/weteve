"""Widget Qt «Log tail»: tail -f de un fichero de log con scroll inteligente.

Comportamiento:
  - lee el fichero con QTimer (polling cada 500 ms) y añade solo las líneas
    nuevas (se guarda el offset de bytes leído),
  - autoscroller al final mientras el usuario esté en la última línea; si
    sube a leer, se pausa y aparece un botón «Ir al final» para reanudar,
  - límite de líneas en memoria (evita crecimiento indefinido),
  - maneja rotación (log truncado o reemplazado: reinicia el offset).
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

POLL_MS = 500
MAX_LINES = 5000
MAX_READ_CHUNK = 262_144  # 256 KB por tick: suficiente y acota memoria

class LogTailWidget(QWidget):
    """Sigue un fichero de log en vivo (tail -f) con autoscroll inteligente."""

    def __init__(self, log_path: Path | None = None, parent=None):
        super().__init__(parent)
        self._path = Path(log_path) if log_path else None
        self._offset = 0
        self._carry = b""  # última línea parcial (aún sin \n)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setFont(QFont("Consolas", 9))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.view)

        bottom = QHBoxLayout()
        self.follow_btn = QPushButton("&Ir al final (reanudar)")
        self.follow_btn.setVisible(False)  # solo cuando el follow está pausado
        self.follow_btn.clicked.connect(self.resume_follow)
        bottom.addWidget(self.follow_btn)
        bottom.addStretch()
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        if self._path is not None:
            self._timer.start()
            # Primer volcado: hasta 200 KB de historia, sin autoscroll forzado.
            QTimer.singleShot(0, lambda: self._poll(initial=True))

    # ------------------------------------------------------------------
    def set_log_path(self, path: Path | None) -> None:
        """Cambia el fichero seguido (y limpia la vista)."""
        self._path = Path(path) if path else None
        self._offset = 0
        self._carry = b""
        self.view.clear()
        if self._path is not None and not self._timer.isActive():
            self._timer.start()
        if self._path is not None:
            QTimer.singleShot(0, lambda: self._poll(initial=True))

    def resume_follow(self) -> None:
        """Baja al final y reactiva el autoscroll."""
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.follow_btn.setVisible(False)

    def stop(self) -> None:
        """Detiene el polling (llamar al cerrar el diálogo)."""
        self._timer.stop()

    # ------------------------------------------------------------------
    def _at_bottom(self) -> bool:
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def _poll(self, initial: bool = False) -> None:
        if self._path is None:
            return
        try:
            if not self._path.exists():
                return
            size = self._path.stat().st_size
            # Rotación/truncado: el fichero se hizo más pequeño (o se
            # reemplazó) => reiniciar lectura desde el principio.
            if size < self._offset:
                self._offset = 0
                self._carry = b""
            if size == self._offset:
                return
            with open(self._path, "rb") as f:
                f.seek(self._offset)
                # En el arranque solo mostramos la cola reciente.
                remaining = size - self._offset
                if initial and remaining > 200_000:
                    f.seek(size - 200_000)
                data = f.read(MAX_READ_CHUNK)
                # Consumir hasta el final real del fichero en este tick.
                while True:
                    self._offset += len(data)
                    chunk = self._carry + data
                    *lines, self._carry = chunk.split(b"\n")
                    text = "\n".join(
                        l.decode("utf-8", errors="replace").rstrip("\r")
                        for l in lines
                    )
                    if text:
                        self._append_lines(text)
                    if self._offset >= size:
                        break
                    data = f.read(MAX_READ_CHUNK)
        except OSError:
            # Fichero bloqueado por el escritor en Windows: se reintenta.
            return

    def _append_lines(self, text: str) -> None:
        # Autoscroll solo si el usuario estaba al final; si estaba subido a
        # leer, se pausa y se ofrece el botón para reanudar.
        sb = self.view.verticalScrollBar()
        was_bottom = self._at_bottom()
        self.view.appendPlainText(text)
        if was_bottom:
            sb.setValue(sb.maximum())
        else:
            self.follow_btn.setVisible(True)
