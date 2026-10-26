"""Filesystem path resolution shared by headless backend and Qt UI.

Kept free of PySide6 imports so storage/parser tests can resolve bundled
resources without a display server.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def resource_path(relative: str) -> Path:
    """Resolve an app/resources file in source checkout and onefile exe."""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "app" / "resources" / relative
    return _REPO_ROOT / "app" / "resources" / relative


def preset_path(filename: str) -> Path:
    """Resolve a demo catalog from presets/ in checkout and PyInstaller bundle."""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "presets" / filename
    return _REPO_ROOT / "presets" / filename
