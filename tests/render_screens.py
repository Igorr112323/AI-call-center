"""Render every workspace to PNG for visual review (offscreen, no GL needed)."""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppSettings
from app.core.seed import seed_if_empty
from app.core.storage import Storage
from app.theme import load_stylesheet
from app.ui.main_window import MainWindow

out = Path(__file__).resolve().parents[1] / "tests" / "screens"
out.mkdir(exist_ok=True)

app = QApplication(sys.argv)
app.setStyleSheet(load_stylesheet())

with tempfile.TemporaryDirectory() as tmp:
    settings = AppSettings.load(Path(tmp) / "settings.json")
    storage = Storage(Path(tmp) / "agent.db")
    seed_if_empty(storage)
    win = MainWindow(settings, storage, engine_thread=None)
    win.resize(1280, 800)
    win.show()
    app.processEvents()

    def settle():
        QTimer.singleShot(40, lambda: None)
        app.processEvents()

    win._switch_page(0)
    settle()
    win.grab().save(str(out / "01_registry.png"))

    win._switch_page(1)
    settle()
    win.grab().save(str(out / "02_scenario.png"))

    win._switch_page(2)
    settle()
    win.catalog_tab.fuzzy_box.edit.setText("актуатор 24В 300мм")
    settle()
    win.grab().save(str(out / "03_catalog_local.png"))

    win.catalog_tab.btn_one_c.click()
    settle()
    win.grab().save(str(out / "04_catalog_1c.png"))

    win._switch_page(3)
    settle()
    win.grab().save(str(out / "05_telephony.png"))

    win._switch_page(0)
    settle()
    win.grab().save(str(out / "06_registry_order.png"))
    print("rendered", sorted(p.name for p in out.glob("*.png")))
