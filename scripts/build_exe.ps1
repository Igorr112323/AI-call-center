# Build the monolithic Windows executable.
# Prerequisites: Python 3.11 x64, `pip install -r requirements.txt`
$ErrorActionPreference = "Stop"

Write-Host "==> Cleaning previous build artifacts"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Rendering QSS raster assets"
python scripts/build_assets.py

Write-Host "==> Running test suite"
python -m pytest tests --ignore=tests/ui_smoke.py

Write-Host "==> Building VoiceSalesAgent.exe (onefile)"
pyinstaller VoiceSalesAgent.spec --noconfirm

Write-Host "==> Done: dist/VoiceSalesAgent.exe"
