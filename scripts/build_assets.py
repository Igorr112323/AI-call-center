"""Render QSS-raster assets (combo chevron, checkbox tick) from the SVG icon set.

Run once when icon geometry changes; PNGs are committed alongside style.qss:
    python scripts/build_assets.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Asset rendering needs no visible window; offscreen works on headless CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

OUT = Path(__file__).resolve().parents[1] / "app" / "resources"

_SVG = {
    "chevron-down": ['<polyline points="6 9 12 15 18 9"/>'],
    "check": ['<polyline points="20 6 9 17 4 12"/>'],
}


def render(name: str, color: str, size: int, stroke_width: float = 2.2) -> bytes:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{"".join(_SVG[name])}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()

    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(buf.data())


def render_app_icon() -> bytes:
    """Dark-slate rounded tile with the emerald mic mark, Windows .ico."""
    size = 256
    tile = QPixmap(QSize(size, size))
    tile.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#16191F"))
    painter.drawRoundedRect(0, 0, size, size, 48, 48)
    painter.end()

    from app.ui import icons
    glyph = icons.icon("mic", "#10B981", 128).pixmap(128, 128)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    offset = (size - 128) // 2
    painter.drawPixmap(offset, offset, glyph)
    painter.end()

    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    tile.save(buf, "ICO")
    return bytes(buf.data())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "chevron-down.png").write_bytes(render("chevron-down", "#565E6C", 12))
    (OUT / "check.png").write_bytes(render("check", "#062018", 12, stroke_width=3.0))
    (OUT / "app.ico").write_bytes(render_app_icon())
    print("assets written to", OUT)


if __name__ == "__main__":
    app_ok = True
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    main()
