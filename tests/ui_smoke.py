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
    win.catalog_tab._save()
    win.telephony_tab._save()
    app.processEvents()

    # standalone catalog: CSV import + fuzzy matching + segmented switch
    import csv as _csv
    price_csv = Path(tmp) / "price.csv"
    with price_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = _csv.writer(fh, delimiter=";")
        writer.writerow(["Артикул", "Наименование", "Категория", "Стоимость", "Кол-во"])
        writer.writerow(["TST-1", "Тестовый актуатор 24В 300мм", "актуатор", "5000,00", "9"])
    win.catalog_tab._import_file(str(price_csv))
    assert win.catalog_tab.provider.count() >= 1
    win.catalog_tab.fuzzy_box.edit.setText("актуатор 24в 300мм")
    app.processEvents()
    assert win.catalog_tab.fuzzy_result.text and "TST-1" in win.catalog_tab.fuzzy_result.text()
    win.catalog_tab.btn_one_c.click()
    app.processEvents()
    assert win.catalog_tab.stack.currentIndex() == 1
    win.catalog_tab.btn_local.click()
    assert win.catalog_tab.stack.currentIndex() == 0

    # call control bar exists and reflects engine availability
    assert win.call_bar.active is False
    win.call_bar.set_audio_available(False)
    assert win.call_bar.toggle_btn.isEnabled() is False
    win.call_bar.set_audio_available(True)
    assert win.call_bar.toggle_btn.isEnabled() is True

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

    # ---------------------------------------------------- text call simulator
    from app.ui.text_call_window import TextCallWindow

    def wait_until(condition, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline and not condition():
            app.processEvents()
            time.sleep(0.03)
        assert condition(), "condition timed out"

    sim = TextCallWindow(thread, settings)
    sim.show()
    wait_until(lambda: sim.agent.active)
    app.processEvents()

    sim._send("Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
    wait_until(lambda: "RTT" in sim.rtt_label.text() and sim.rtt_label.text() != "RTT: —")
    assert sim.slots_table.rowCount() >= 2
    assert sim.tool_host.count() >= 1

    sim._send("Да, оформляем заказ")
    wait_until(lambda: sim.state_badge.text() == "DONE")
    app.processEvents()
    sim.grab().save(str(Path("tests/screens/07_text_simulator.png")))

    sim._on_hangup()
    wait_until(lambda: not sim.agent.active)
    deadline = time.time() + 2
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert sim.state_badge.text() == "ЗАВЕРШЁН"
    assert sim.close_btn.isVisible() and sim.hangup_btn.isHidden()
    assert not sim.input.isEnabled()
    assert all(not chip.isEnabled() for chip in sim._chips)
    sim.grab().save(str(Path("tests/screens/08_text_simulator_done.png")))
    sim.close()

    # the finished text call lands in the registry immediately with an order
    win._switch_page(0)
    win.calls_tab.refresh()
    app.processEvents()
    text_calls = [c for c in win.calls_tab._records if c.source == "text"]
    assert len(text_calls) == 1
    finished = text_calls[0]
    assert finished.order is not None
    row = next(i for i, c in enumerate(win.calls_tab._records) if c.id == finished.id)
    win.calls_tab.table.selectRow(row)
    app.processEvents()
    assert win.calls_tab.inspector.order_card.quote_btn.isEnabled()
    assert not win.calls_tab.inspector.source_badge.isHidden()
    win.grab().save(str(Path("tests/screens/09_registry_text_call.png")))

    # opening the quote produces a real PDF through the running engine
    open_future = thread.submit(thread.engine.quote_pdf_path(finished.order.id))
    wait_until(lambda: open_future.done())
    assert Path(open_future.result()).exists()

    # busy guard on the control bar round-trips
    win.call_bar.set_busy(True)
    assert not win.call_bar.text_btn.isEnabled()
    win.call_bar.set_busy(False)
    assert win.call_bar.text_btn.isEnabled()

    thread.graceful_stop()
    assert not thread.isRunning()

    print("UI smoke OK —", win.calls_tab.table.rowCount(), "calls rendered")
