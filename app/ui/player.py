"""Compact call recording player: 28 px transport, thin timeline, speed control."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from ..core.audio import format_timecode
from ..theme import PALETTE
from . import icons

try:  # backend missing in minimal Linux/headless or stripped Windows builds
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    MEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    MEDIA_AVAILABLE = False


class PlayerBar(QFrame):
    playback_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SubSurface")
        self._duration_ms = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setIcon(icons.icon("play", PALETTE["canvas"], 13))
        self.play_btn.setIconSize(self.play_btn.iconSize())
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setProperty("role", "timecode")

        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setObjectName("Timeline")
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self._seek)

        self.speed = QComboBox()
        self.speed.addItems(["1.0x", "1.2x", "1.5x"])
        self.speed.setCurrentText("1.0x")
        self.speed.currentTextChanged.connect(self._set_rate)
        self.speed.setFixedWidth(66)

        controls.addWidget(self.play_btn)
        controls.addWidget(self.time_label, 0)
        controls.addWidget(self.timeline, 1)
        controls.addWidget(self.speed, 0)
        root.addLayout(controls)

        self._player = None
        self._audio = None
        if MEDIA_AVAILABLE:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._audio.setVolume(0.9)
            self._player.positionChanged.connect(self._position_changed)
            self._player.durationChanged.connect(self._duration_changed)
            self._player.playbackStateChanged.connect(self._state_changed)
            self._player.errorOccurred.connect(self._on_error)
        else:
            self.play_btn.setEnabled(False)
            self.timeline.setEnabled(False)
            self.speed.setEnabled(False)
            self.time_label.setText("Аудиостек недоступно")

    def load(self, path: str | None) -> None:
        if not self._player:
            return
        self._player.stop()
        if not path:
            self.timeline.setRange(0, 0)
            self.time_label.setText("00:00 / 00:00")
            self.play_btn.setEnabled(False)
            return
        self.play_btn.setEnabled(True)
        self._player.setSource(QUrl.fromLocalFile(path))

    def release(self) -> None:
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())

    # ------------------------------------------------------------------ slots
    def _toggle(self) -> None:
        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek(self, position: int) -> None:
        if self._player:
            self._player.setPosition(position)

    def _set_rate(self, text: str) -> None:
        if self._player:
            self._player.setPlaybackRate(float(text.rstrip("x")))

    def _position_changed(self, pos: int) -> None:
        if not self.timeline.isSliderDown():
            self.timeline.setValue(pos)
        self._refresh_time(pos)

    def _duration_changed(self, duration: int) -> None:
        self._duration_ms = duration
        self.timeline.setRange(0, duration)

    def _state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        color = PALETTE["canvas"]
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", color, 13))

    def _refresh_time(self, pos: int) -> None:
        self.time_label.setText(
            f"{format_timecode(pos / 1000)} / {format_timecode(self._duration_ms / 1000)}"
        )

    def _on_error(self, _error, message: str) -> None:
        if message:
            self.playback_error.emit(message)
