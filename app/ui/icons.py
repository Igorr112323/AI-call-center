"""Monochrome stroke icons (Feather/Lucide geometry, MIT-licensed style).

SVG is rendered through QSvgRenderer; ``currentColor`` is replaced per call so
one source serves active/inactive/disabled states without duplicate assets.
No emoji anywhere in the product.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_ICONS: dict[str, list[str]] = {
    "search": [
        '<circle cx="11" cy="11" r="7"/>',
        '<line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    ],
    "phone": [
        '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.37 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.33 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    ],
    "play": ['<polygon points="6 4 20 12 6 20 6 4"/>'],
    "pause": ['<rect x="6" y="4" width="4" height="16" rx="1"/>',
              '<rect x="14" y="4" width="4" height="16" rx="1"/>'],
    "database": [
        '<ellipse cx="12" cy="5" rx="8" ry="3"/>',
        '<path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>',
        '<path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>',
    ],
    "messages": [
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    ],
    "sliders": [
        '<line x1="4" y1="21" x2="4" y2="14"/>',
        '<line x1="4" y1="10" x2="4" y2="3"/>',
        '<line x1="12" y1="21" x2="12" y2="12"/>',
        '<line x1="12" y1="8" x2="12" y2="3"/>',
        '<line x1="20" y1="21" x2="20" y2="16"/>',
        '<line x1="20" y1="12" x2="20" y2="3"/>',
        '<line x1="2" y1="14" x2="6" y2="14"/>',
        '<line x1="10" y1="8" x2="14" y2="8"/>',
        '<line x1="18" y1="16" x2="22" y2="16"/>',
    ],
    "tower": [
        '<circle cx="12" cy="12.5" r="1.5"/>',
        '<path d="M8.7 9.2a4.7 4.7 0 0 0 0 6.6"/>',
        '<path d="M15.3 9.2a4.7 4.7 0 0 1 0 6.6"/>',
        '<path d="M6.3 7a8 8 0 0 0 0 11"/>',
        '<path d="M17.7 7a8 8 0 0 1 0 11"/>',
        '<path d="M9.5 16 8.5 21h7l-1-5"/>',
    ],
    "check": ['<polyline points="20 6 9 17 4 12"/>'],
    "x": ['<line x1="18" y1="6" x2="6" y2="18"/>', '<line x1="6" y1="6" x2="18" y2="18"/>'],
    "chevron-down": ['<polyline points="6 9 12 15 18 9"/>'],
    "chevron-right": ['<polyline points="9 6 15 12 9 18"/>'],
    "send": ['<line x1="22" y1="2" x2="11" y2="13"/>',
             '<polygon points="22 2 15 22 11 13 2 9 22 2"/>'],
    "plus": ['<line x1="12" y1="5" x2="12" y2="19"/>', '<line x1="5" y1="12" x2="19" y2="12"/>'],
    "volume": [
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>',
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
    ],
    "gauge": [
        '<path d="m12 14 4-4"/>',
        '<path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    ],
    "file": [
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>',
        '<polyline points="14 2 14 8 20 8"/>',
        '<line x1="16" y1="13" x2="8" y2="13"/>',
        '<line x1="16" y1="17" x2="8" y2="17"/>',
    ],
    "rotate": [
        '<polyline points="1 4 1 10 7 10"/>',
        '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    ],
    "mic": [
        '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>',
        '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>',
        '<line x1="12" y1="19" x2="12" y2="23"/>',
        '<line x1="8" y1="23" x2="16" y2="23"/>',
    ],
    "trash": [
        '<polyline points="3 6 5 6 21 6"/>',
        '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    ],
    "zap": ['<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'],
    "package": [
        '<path d="M16.5 9.4 7.55 4.24"/>',
        '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>',
        '<polyline points="3.29 7 12 12 20.71 7"/>',
        '<line x1="12" y1="22" x2="12" y2="12"/>',
    ],
    "user": [
        '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>',
        '<circle cx="12" cy="7" r="4"/>',
    ],
    "arrow-up": ['<line x1="12" y1="19" x2="12" y2="5"/>',
                 '<polyline points="5 12 12 5 19 12"/>'],
}

_CACHE: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str = "#7A8394", size: int = 16) -> QIcon:
    key = (name, color, size)
    if key in _CACHE:
        return _CACHE[key]
    elements = _ICONS.get(name, _ICONS["file"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round">{"".join(elements)}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    qicon = QIcon(pixmap)
    _CACHE[key] = qicon
    return qicon


def tinted_icon(name: str, color: QColor, size: int = 16) -> QIcon:
    return icon(name, color.name(), size)
