# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the monolithic Windows .exe.

Build:  pyinstaller VoiceSalesAgent.spec --noconfirm
Output: dist/VoiceSalesAgent.exe (onefile, windowed, no console window)
"""
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

datas = [
    ("app/resources/style.qss", "app/resources"),
    ("app/resources/chevron-down.png", "app/resources"),
    ("app/resources/check.png", "app/resources"),
    # embedded Cyrillic TTFs for PDF generation + their free-license notice
    ("app/resources/fonts/DejaVuSans.ttf", "app/resources/fonts"),
    ("app/resources/fonts/DejaVuSans-Bold.ttf", "app/resources/fonts"),
    ("app/resources/fonts/DejaVu-LICENSE.txt", "app/resources/fonts"),
    # one-click demo catalogs, resolved via app.paths.preset_path at runtime
    ("presets/actuators_catalog.xlsx", "presets"),
    ("presets/rvd_hydraulics_catalog.xlsx", "presets"),
    ("presets/corrugated_boxes_catalog.xlsx", "presets"),
]
datas += collect_data_files("PySide6", include_py_files=False)
# reportlab ships font/resource data used at PDF build time
datas += collect_data_files("reportlab")
# sounddevice loads the bundled PortAudio shared library at import time
datas += collect_data_files("sounddevice")

binaries = collect_dynamic_libs("sounddevice")

hiddenimports = collect_submodules("app")
# QtSvg backs the monochrome icon renderer; QtMultimedia backs the call player
hiddenimports += ["PySide6.QtSvg", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]
# standalone-mode runtimes
hiddenimports += collect_submodules("openpyxl")
# whole-package collection: reportlab.colors pulls the rl_accel C extension
# and platypus resolves several submodules lazily at document build time
hiddenimports += collect_submodules("reportlab")
binaries += collect_dynamic_libs("reportlab")
hiddenimports += ["sounddevice", "numpy"]
# comtypes drives the in-process SAPI voice; dynamic dispatch avoids the
# generated-wrapper cache, but the package submodules must still be bundled
hiddenimports += collect_submodules("comtypes")
# Piper neural voice: engine package + ONNX Runtime (espeak-ng data ships
# inside the piper wheel, so collect_all is required, not just submodules)
for _pkg in ("piper", "onnxruntime"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h
# Edge online neural voice: pure-python websocket client plus aiohttp;
# soundfile bundles the libsndfile DLL used to decode its MP3 stream
for _pkg in ("edge_tts", "aiohttp", "soundfile"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h
# the neural voice model itself is downloaded by CI before packaging
_piper_dir = Path("app/resources/piper")
if _piper_dir.is_dir():
    for _model_file in _piper_dir.iterdir():
        if _model_file.is_file() and _model_file.name != ".gitkeep":
            datas.append((str(_model_file), "app/resources/piper"))

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy optional ML stacks (Silero VAD via torch.hub, faster-whisper,
    # F5-TTS) are documented add-ons, downloaded on demand — not bundled.
    excludes=["tkinter", "matplotlib", "pytest", "torch", "faster_whisper",
              "f5_tts", "xlrd", "pymupdf", "fitz"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VoiceSalesAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI application
    disable_windowed_traceback=False,
    # rendered by scripts/build_assets.py from the monochrome mic mark
    icon="app/resources/app.ico",
)
