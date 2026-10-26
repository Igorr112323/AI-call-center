"""Top control bar: softphone mic test call, live timer and VU meter."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ..core.audio import format_timecode
from ..theme import PALETTE
from . import icons


class VUMeter(QWidget):
    """14-segment solid bar; cold segments muted, hot segments accent/danger."""

    SEGMENTS = 14
    MIN_DB, MAX_DB = -50.0, -3.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = self.MIN_DB
        self._peak = self.MIN_DB
        self.setFixedHeight(16)
        self.setMinimumWidth(150)

    def set_level_db(self, db: float) -> None:
        self._level = max(self.MIN_DB, min(self.MAX_DB, db))
        self._peak = max(self._peak * 0.92, self._level)  # slow decay peak hold
        self.update()

    def reset(self) -> None:
        self._level = self._peak = self.MIN_DB
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.NoPen)
        ratio = (self._level - self.MIN_DB) / (self.MAX_DB - self.MIN_DB)
        active = round(ratio * self.SEGMENTS)
        gap, w, h = 3, 7, 14
        for i in range(self.SEGMENTS):
            x = i * (w + gap)
            if i < active:
                if i >= 11:
                    color = QColor(PALETTE["danger"])
                elif i >= 8:
                    color = QColor(PALETTE["warning"])
                else:
                    color = QColor(PALETTE["accent"])
            else:
                color = QColor(PALETTE["border_strong"])
            painter.setBrush(color)
            painter.drawRoundedRect(x, 1, w, h, 1.5, 1.5)
        painter.end()


class CallControlBar(QFrame):
    start_local_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CallBar")
        self._elapsed = 0
        self._active = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        self.toggle_btn = QPushButton("  Начать тестовый звонок с микрофона")
        self.toggle_btn.setObjectName("CallToggle")
        self.toggle_btn.setIcon(icons.icon("mic", PALETTE["accent"]))
        self.toggle_btn.setIconSize(QSize(15, 15))
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._on_click)
        layout.addWidget(self.toggle_btn)

        self.status_dot = QLabel()
        self.status_dot.setPixmap(_dot(PALETTE["text_faint"]))
        self.status_label = QLabel("Softphone готов")
        self.status_label.setProperty("role", "muted")
        layout.addWidget(self.status_dot)
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        self.vu = VUMeter()
        layout.addWidget(self.vu)

        self.timer_label = QLabel("00:00")
        self.timer_label.setProperty("role", "timecode")
        layout.addWidget(self.timer_label)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def set_audio_available(self, available: bool) -> None:
        self.toggle_btn.setEnabled(available)
        if not available:
            self.status_label.setText("Микрофон недоступен (нет PortAudio/sounddevice)")
            self.status_dot.setPixmap(_dot(PALETTE["danger"]))

    def call_started(self, mode: str) -> None:
        self._active = True
        self._elapsed = 0
        self.vu.reset()
        self.timer_label.setText("00:00")
        self.toggle_btn.setText("  Завершить звонок")
        self.toggle_btn.setIcon(icons.icon("x", PALETTE["danger"]))
        self.status_dot.setPixmap(_dot(PALETTE["accent"]))
        self.status_label.setText(
            "Идёт локальный разговор" if mode == "local" else f"Активен звонок · {mode}")
        self._timer.start()

    def call_ended(self) -> None:
        self._active = False
        self._timer.stop()
        self.vu.reset()
        self.toggle_btn.setText("  Начать тестовый звонок с микрофона")
        self.toggle_btn.setIcon(icons.icon("mic", PALETTE["accent"]))
        self.status_dot.setPixmap(_dot(PALETTE["text_faint"]))
        self.status_label.setText("Softphone готов")

    def update_level(self, db: float) -> None:
        if self._active:
            self.vu.set_level_db(db)

    @property
    def active(self) -> bool:
        return self._active

    def _tick(self) -> None:
        self._elapsed += 1
        self.timer_label.setText(format_timecode(self._elapsed))

    def _on_click(self) -> None:
        if self._active:
            self.stop_requested.emit()
        else:
            self.start_local_requested.emit()


def _dot(color_hex: str) -> "QPixmap":
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap
    pixmap = QPixmap(QSize(8, 8))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color_hex))
    painter.drawEllipse(0, 1, 8, 8)
    painter.end()
    return pixmap
