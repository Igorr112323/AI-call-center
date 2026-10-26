"""Convenience launcher: python run.py

Modes:
    python run.py              launch the Qt application
    python run.py --selfcheck  headless import/resource check, exit code 0/1
"""
import sys


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
        for filename in (
            "actuators_catalog.xlsx",
            "rvd_hydraulics_catalog.xlsx",
            "corrugated_boxes_catalog.xlsx",
        ):
            assert preset_path(filename).exists(), filename
        assert extract_params("24 вольта, ход 300 мм")["voltage"] == 24
        assert amount_in_words(28800.5).startswith("Двадцать восемь тысяч")
        assert CallEngine and EngineThread and DialogManager and TextSimulatorAgent
        assert AppSettings
        print("selfcheck OK")
        return 0
    except Exception as exc:  # noqa: BLE001 - build gate needs the message
        print(f"selfcheck FAILED: {exc!r}")
        return 1


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(selfcheck())
    from app.main import main

    raise SystemExit(main())
