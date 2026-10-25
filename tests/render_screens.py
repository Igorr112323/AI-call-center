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

    names = ["01_registry", "02_scenario", "03_onec", "04_telephony"]
    for i, name in enumerate(names):
        win._switch_page(i)
        app.processEvents()
        QTimer.singleShot(50, lambda: None)
        app.processEvents()
        win.grab().save(str(out / f"{name}.png"))

    # inspector with an order selected
    win._switch_page(0)
    app.processEvents()
    win.grab().save(str(out / "05_registry_order.png"))
    print("rendered", sorted(p.name for p in out.glob("*.png")))
