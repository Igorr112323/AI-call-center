# Build the monolithic Windows executable.
# Prerequisites: Python 3.11 x64, `pip install -r requirements.txt`
$ErrorActionPreference = "Stop"

function Assert-NativeExit {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "Step '$Step' failed with exit code $LASTEXITCODE"
    }
}

Write-Host "==> Cleaning previous build artifacts"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Rendering QSS raster assets and application icon"
python scripts/build_assets.py
Assert-NativeExit "build_assets"

Write-Host "==> Running backend test suite"
python -m pytest tests --ignore=tests/ui_smoke.py
Assert-NativeExit "pytest"

Write-Host "==> Building VoiceSalesAgent.exe (onefile)"
pyinstaller VoiceSalesAgent.spec --noconfirm
Assert-NativeExit "pyinstaller"

Write-Host "==> Smoke-launching the bundled executable (--selfcheck)"
if (-not (Test-Path "dist/VoiceSalesAgent.exe")) {
    throw "dist/VoiceSalesAgent.exe was not produced"
}
& ".\dist\VoiceSalesAgent.exe" --selfcheck
Assert-NativeExit "exe selfcheck"

Write-Host "==> Done: dist/VoiceSalesAgent.exe verified"
