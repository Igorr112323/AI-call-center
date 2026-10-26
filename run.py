"""Convenience launcher: python run.py

Modes:
    python run.py              launch the Qt application
    python run.py --selfcheck  headless import/resource check, exit code 0/1
"""
import sys
import tempfile
import traceback
from pathlib import Path

SELFCHECK_LOG = Path(tempfile.gettempdir()) / "voice_sales_selfcheck.log"


def _log(message: str) -> None:
    """Windowed PyInstaller builds have no stdout; mirror output to a log file."""
    try:
        print(message)
    except Exception:  # noqa: BLE001 - sys.stdout may be None in windowed mode
        pass
    try:
        SELFCHECK_LOG.write_text(message, encoding="utf-8")
    except OSError:
        pass


def selfcheck() -> int:
    """Verify every runtime module and bundled resource is reachable."""
    try:
        from app.config import AppSettings
        from app.core.engine import CallEngine, EngineThread
        from app.core.storage import Storage
        from app.brain.dialog_manager import DialogManager
        from app.integrations.catalog_provider import extract_params
        from app.paths import preset_path, resource_path
        from app.reporting.quote_generator import amount_in_words
        from app.telephony.text_simulator import TextSimulatorAgent

        assert resource_path("style.qss").exists()
        assert resource_path("fonts/DejaVuSans.ttf").exists()
        # Frozen builds must ship the neural voice; source checkouts may
        # legitimately lack it (model is downloaded only on the build host)
        if getattr(sys, "frozen", False):
            from app.voice.piper_tts import piper_model_paths

            assert piper_model_paths() is not None, "bundled Piper voice missing"
        for filename in (
            "actuators_catalog.xlsx",
            "rvd_hydraulics_catalog.xlsx",
            "corrugated_boxes_catalog.xlsx",
        ):
            assert preset_path(filename).exists(), filename
        assert extract_params("24 вольта, ход 300 мм")["voltage"] == 24
        assert amount_in_words(28800.5).startswith("Двадцать восемь тысяч")
        assert CallEngine and EngineThread and DialogManager and TextSimulatorAgent
        assert AppSettings and Storage
        _log("selfcheck OK")
        return 0
    except Exception:  # noqa: BLE001 - build gate needs the traceback on disk
        _log(f"selfcheck FAILED:\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(selfcheck())
    from app.main import main

    raise SystemExit(main())
