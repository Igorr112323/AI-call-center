# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the monolithic Windows .exe.

Build:  pyinstaller VoiceSalesAgent.spec --noconfirm
Output: dist/VoiceSalesAgent.exe (onefile, windowed, no console window)
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("app/resources/style.qss", "app/resources"),
    ("app/resources/chevron-down.png", "app/resources"),
    ("app/resources/check.png", "app/resources"),
]
datas += collect_data_files("PySide6", include_py_files=False)

hiddenimports = collect_submodules("app")
# QtSvg backs the monochrome icon renderer; QtMultimedia backs the call player
hiddenimports += ["PySide6.QtSvg", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy optional ML stacks are deployed as documented add-ons, not bundled
    excludes=["tkinter", "matplotlib", "pytest", "torch", "faster_whisper", "f5_tts"],
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
    icon=None,               # drop app.ico next to the spec and set icon="app.ico"
)
