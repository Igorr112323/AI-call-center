"""Theme bootstrap: QSS loading compatible with PyInstaller bundles."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont

# Mirrored tokens from resources/style.qss — use in code-painted elements
# (status dots, custom delegates) so colors stay in one design system.
PALETTE = {
    "canvas": "#0F1115",
    "surface": "#16191F",
    "subsurface": "#121418",
    "border": "#222730",
    "border_strong": "#2C323D",
    "text": "#E6E8EC",
    "text_muted": "#7A8394",
    "text_faint": "#565E6C",
    "accent": "#10B981",
    "accent_dim": "#0C8F65",
    "danger": "#E5484D",
    "warning": "#D9A13F",
    "row_hover": "#1A1E26",
    "selected": "#172622",
}


def resource_path(relative: str) -> Path:
    """Resolve a resource both in source checkout and inside onefile exe."""
    from .paths import resource_path as _resolve
    return _resolve(relative)


def preset_path(filename: str) -> Path:
    """Resolve a demo catalog from presets/ in checkout and PyInstaller bundle."""
    from .paths import preset_path as _resolve
    return _resolve(filename)


def load_stylesheet() -> str:
    qss = resource_path("style.qss").read_text(encoding="utf-8")
    # QSS url() tokens need absolute paths; they resolve inside the PyInstaller
    # bundle via resource_path() (sys._MEIPASS) as well as in a source checkout.
    for token, filename in (
        ("@CHEVRON_URL@", "chevron-down.png"),
        ("@CHECK_URL@", "check.png"),
    ):
        url = resource_path(filename).as_posix()
        qss = qss.replace(token, url)
    return qss


def application_font() -> QFont:
    font = QFont("Segoe UI Variable", 9)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


STATUS_COLORS = {
    "order": QColor(PALETTE["accent"]),
    "consultation": QColor(PALETTE["text_faint"]),
    "failed": QColor(PALETTE["danger"]),
    "ongoing": QColor(PALETTE["warning"]),
}
