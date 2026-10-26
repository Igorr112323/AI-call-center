"""Reusable compact widgets: status dot, badges, cards, labeled fields."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)


class ScrollArea(QScrollArea):
    """Canvas-colored scroll container for settings workspaces."""

    def __init__(self, widget: QWidget, parent=None):
        super().__init__(parent)
        self.setWidget(widget)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setAutoFillBackground(False)

from ..theme import PALETTE, STATUS_COLORS
from . import icons


class StatusDot(QWidget):
    """6 px semantic dot — the only color-coded element in the registry."""

    def __init__(self, status: str = "consultation", parent=None):
        super().__init__(parent)
        self._status = status
        self.setFixedSize(12, 12)

    def set_status(self, status: str) -> None:
        self._status = status
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = STATUS_COLORS.get(self._status, QColor(PALETTE["text_faint"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(3, 3, 6, 6)
        painter.end()


class Badge(QLabel):
    def __init__(self, text: str = "", tone: str = "", parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        if tone:
            self.setProperty("badge", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_tone(self, tone: str) -> None:
        self.setProperty("badge", tone or "")
        self.style().unpolish(self)
        self.style().polish(self)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("card", True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)

    def add(self, widget: QWidget) -> None:
        if isinstance(widget, QLayout):
            self._layout.addLayout(widget)
        else:
            self._layout.addWidget(widget)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self._layout.addWidget(widget, stretch)

    def add_layout(self, layout: QLayout) -> None:
        self._layout.addLayout(layout)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setProperty("role", "sectionLabel")


class IconButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setIcon(icons.icon(icon_name))
        self.setIconSize(QSize(15, 15))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class SearchBox(QFrame):
    text_changed = Signal(str)

    def __init__(self, placeholder: str = "Поиск по номеру или товару", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.icon("search", PALETTE["text_faint"], 14).pixmap(14, 14))
        self.edit = QLineEdit()
        self.edit.setProperty("role", "search")
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.text_changed.emit)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.edit)

    def text(self) -> str:
        return self.edit.text()


class LabeledField(QWidget):
    """Compact label-above-input field, no verbose helper text."""

    def __init__(self, label: str, widget: QWidget, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        caption = QLabel(label)
        caption.setProperty("role", "fieldLabel")
        box.addWidget(caption)
        box.addWidget(widget)
        self.widget = widget


def hline() -> QFrame:
    line = QFrame()
    line.setProperty("separator", True)
    line.setFrameShape(QFrame.Shape.HLine)
    return line


def mono_label(text: str = "", role: str = "mono") -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


def make_combo(items: list[str], current: str | None = None) -> QComboBox:
    combo = QComboBox()
    combo.addItems(items)
    if current:
        combo.setCurrentText(current)
    return combo


def make_spin(value: int, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix)
    return compact_spin(spin)


class IntEdit(QLineEdit):
    """Numeric text field with range validation.

    Replaces QSpinBox: Qt's stylesheet style collapses the internal line-edit
    geometry to 1px when NoButtons is combined with QSS styling.
    """

    def __init__(self, value: int = 0, minimum: int = 0, maximum: int = 999999,
                 suffix: str = "", parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QIntValidator
        self._min, self._max, self._suffix = minimum, maximum, suffix
        self.setValidator(QIntValidator(minimum, maximum, self))
        self.setValue(value)

    def value(self) -> int:
        digits = "".join(ch for ch in self.text() if ch.isdigit() or ch == "-")
        try:
            return max(self._min, min(self._max, int(digits or 0)))
        except ValueError:
            return self._min

    def setValue(self, value: int) -> None:
        self.setText(f"{int(value)}{self._suffix}")

    def setRange(self, minimum: int, maximum: int) -> None:
        from PySide6.QtGui import QIntValidator
        self._min, self._max = minimum, maximum
        self.setValidator(QIntValidator(minimum, maximum, self))

    def setSuffix(self, suffix: str) -> None:
        value = self.value()
        self._suffix = suffix
        self.setValue(value)

    def setSingleStep(self, step: int) -> None:  # API parity with QSpinBox
        self._step = step


class FloatEdit(QLineEdit):
    def __init__(self, value: float = 1.0, minimum: float = 0.0, maximum: float = 99.0,
                 decimals: int = 2, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QDoubleValidator
        self._min, self._max, self._decimals = minimum, maximum, decimals
        self.setValidator(QDoubleValidator(minimum, maximum, decimals, self))
        self.setValue(value)

    def value(self) -> float:
        try:
            return max(self._min, min(self._max, float(self.text().replace(",", "."))))
        except ValueError:
            return self._min

    def setValue(self, value: float) -> None:
        self.setText(f"{value:.{self._decimals}f}")

    def setRange(self, minimum: float, maximum: float) -> None:
        from PySide6.QtGui import QDoubleValidator
        self._min, self._max = minimum, maximum
        self.setValidator(QDoubleValidator(minimum, maximum, self._decimals, self))


def compact_spin(spin: QSpinBox) -> QSpinBox:
    """Retained for API compatibility; prefer IntEdit/FloatEdit in new code."""
    spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
    return spin


def make_checkbox(text: str, checked: bool = False) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    return box


class ExpandingSpacer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
