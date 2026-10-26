"""Render every workspace and the text simulator to PNG for visual review.

Output: docs/screenshots/ (committed). Offscreen, no GL needed:
    LD_LIBRARY_PATH=tests/stubs/build QT_QPA_PLATFORM=offscreen \
        PYTHONPATH=. python tests/render_screens.py

The PDF page render is optional: install pymupdf temporarily to (re)render
the quote screenshot; the rest works without it.
"""
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppSettings
from app.core.engine import EngineThread
from app.core.seed import seed_if_empty
from app.core.storage import Storage
from app.theme import load_stylesheet
from app.ui.main_window import MainWindow

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
app.setStyleSheet(load_stylesheet())


def settle():
    QTimer.singleShot(40, lambda: None)
    app.processEvents()


def wait_until(condition, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not condition():
        app.processEvents()
        time.sleep(0.03)
    assert condition(), "render condition timed out"


with tempfile.TemporaryDirectory() as tmp:
    settings = AppSettings.load(Path(tmp) / "settings.json")
    storage = Storage(Path(tmp) / "agent.db")
    seed_if_empty(storage)
    win = MainWindow(settings, storage, engine_thread=None)
    win.resize(1280, 800)
    win.show()
    settle()

    win._switch_page(0)
    settle()
    win.grab().save(str(OUT / "01_registry.png"))

    win._switch_page(1)
    settle()
    win.grab().save(str(OUT / "02_scenario.png"))

    win._switch_page(2)
    settle()
    win.catalog_tab.fuzzy_box.edit.setText("актуатор 24В 300мм")
    settle()
    win.grab().save(str(OUT / "03_catalog_local.png"))

    win.catalog_tab.btn_one_c.click()
    settle()
    win.grab().save(str(OUT / "04_catalog_1c.png"))

    win._switch_page(3)
    settle()
    win.grab().save(str(OUT / "05_telephony.png"))

    win._switch_page(0)
    settle()
    win.grab().save(str(OUT / "06_registry_order.png"))

    # ------------------------------------------------------- engine + simulator
    thread = EngineThread(settings, storage, tmp)
    thread.start()
    deadline = time.time() + 10
    ready = []
    thread.event_received.connect(lambda e: ready.append(e))
    while time.time() < deadline and not any(e.get("type") == "engine_ready" for e in ready):
        app.processEvents()
        time.sleep(0.05)

    # actuator test catalog for a deterministic simulator sale
    import csv as _csv
    price_csv = Path(tmp) / "price.csv"
    with price_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = _csv.writer(fh, delimiter=";")
        writer.writerow(["Артикул", "Наименование", "Категория", "Стоимость", "Кол-во"])
        writer.writerow(["TST-1", "Тестовый актуатор 24В 300мм", "актуатор", "5000,00", "9"])
    win.catalog_tab._import_file(str(price_csv))
    settle()

    from app.ui.text_call_window import TextCallWindow
    sim = TextCallWindow(thread, settings)
    sim.resize(750, 550)
    sim.show()
    wait_until(lambda: sim.agent.active)
    settle()

    sim._send("Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
    wait_until(lambda: sim.rtt_label.text() != "RTT: —")
    settle()
    sim.grab().save(str(OUT / "07_text_simulator.png"))

    sim._send("Да, оформляем заказ")
    wait_until(lambda: sim.state_badge.text() == "DONE")
    settle()

    sim._on_hangup()
    wait_until(lambda: not sim.agent.active)
    settle()
    sim.grab().save(str(OUT / "08_text_simulator_done.png"))
    sim.close()

    win._switch_page(0)
    win.calls_tab.refresh()
    settle()
    win.grab().save(str(OUT / "09_registry_text_call.png"))

    # --------------------------------------------------------------- PDF preview
    try:
        import fitz  # optional, pymupdf not a runtime dependency
        from app.config import CatalogConfig
        from app.core.models import Order, OrderItem
        from app.reporting.quote_generator import generate_quote_pdf

        order = Order(
            id=7, number_1c="ЛОКАЛ-20261026-001",
            delivery_address="г. Краснодар, ул. Промышленная, 12",
            items=[
                OrderItem("ACT-24-300", "Актуатор линейный 24В, ход 300мм, IP65", 2, 11500),
                OrderItem("LSE-IP67", "Концевой выключатель IP67 для конвейера", 4, 1450),
            ],
        )
        pdf = generate_quote_pdf(order, CatalogConfig(), phone="+7 918 204-11-22",
                                 client_name="ООО «Завод металлоконструкций»",
                                 out_dir=Path(tmp))
        page = fitz.open(pdf)[0]
        page.get_pixmap(dpi=110).save(str(OUT / "10_quote_pdf.png"))
    except ImportError:
        print("pymupdf not installed; skipping PDF screenshot (kept existing one if present)")

    thread.graceful_stop()
    print("rendered", sorted(p.name for p in OUT.glob("*.png")))
