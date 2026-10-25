"""Headless UI smoke test (QT_QPA_PLATFORM=offscreen). Not part of pytest."""
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.config import AppSettings
from app.core.seed import seed_if_empty
from app.core.storage import Storage
from app.ui.main_window import MainWindow

app = QApplication(sys.argv)
app.setStyleSheet(Path("app/resources/style.qss").read_text(encoding="utf-8"))

with tempfile.TemporaryDirectory() as tmp:
    settings = AppSettings.load(Path(tmp) / "settings.json")
    storage = Storage(Path(tmp) / "agent.db")
    assert seed_if_empty(storage)
    assert storage.stats()["total"] == 6

    win = MainWindow(settings, storage, engine_thread=None)
    win.show()

    # every page must construct and switch
    for i in range(4):
        win._switch_page(i)
        app.processEvents()
    win._switch_page(0)
    app.processEvents()

    # master/detail wiring
    assert win.calls_tab.table.rowCount() == 6
    win.calls_tab.table.selectRow(0)
    app.processEvents()
    call = win.calls_tab.current_call()
    assert call is not None and call.order is not None
    win.calls_tab._set_filter(call.status.__class__.ORDER)
    app.processEvents()
    assert win.calls_tab.table.rowCount() == 3
    win.calls_tab._set_filter(None)

    # settings tabs round-trip through widgets and back
    win.scenario_tab._save()
    win.one_c_tab._save()
    win.telephony_tab._save()
    app.processEvents()

    # icon factory covers every named icon used in the shell
    from app.ui import icons
    for name in icons._ICONS:
        assert not icons.icon(name).isNull()

    # engine thread boots its asyncio loop and emits engine_ready, then stops cleanly
    from app.core.engine import EngineThread
    import time
    thread = EngineThread(settings, storage, tmp)
    received = []
    thread.event_received.connect(lambda e: received.append(e))
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not any(e.get("type") == "engine_ready" for e in received):
        app.processEvents()
        time.sleep(0.05)
    assert any(e.get("type") == "engine_ready" for e in received)
    thread.graceful_stop()
    assert not thread.isRunning()

    print("UI smoke OK —", win.calls_tab.table.rowCount(), "calls rendered")
