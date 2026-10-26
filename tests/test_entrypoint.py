"""Entry-point and packaging sanity checks (no PyInstaller required)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_run_selfcheck_passes():
    import run

    assert run.selfcheck() == 0


def test_spec_references_only_existing_assets():
    spec = (ROOT / "VoiceSalesAgent.spec").read_text(encoding="utf-8")
    expected = [
        "app/resources/style.qss",
        "app/resources/chevron-down.png",
        "app/resources/check.png",
        "app/resources/app.ico",
        "app/resources/fonts/DejaVuSans.ttf",
        "app/resources/fonts/DejaVuSans-Bold.ttf",
        "presets/actuators_catalog.xlsx",
        "presets/rvd_hydraulics_catalog.xlsx",
        "presets/corrugated_boxes_catalog.xlsx",
    ]
    for relative in expected:
        assert (ROOT / relative).exists(), relative
        assert relative in spec or relative.endswith("app.ico"), relative
    # icon is wired into the EXE block
    assert 'icon="app/resources/app.ico"' in spec


def test_build_script_gates_on_exit_codes_and_selfcheck():
    ps1 = (ROOT / "scripts/build_exe.ps1").read_text(encoding="utf-8")
    assert "Assert-NativeExit" in ps1
    assert "--selfcheck" in ps1
    assert "pytest" in ps1 and "pyinstaller" in ps1
