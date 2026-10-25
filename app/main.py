"""Application entry point: wires storage, engine thread and the Qt shell."""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from .config import AppSettings, user_data_dir
from .core.engine import EngineThread
from .core.seed import seed_if_empty
from .core.storage import Storage
from .logging_setup import setup_logging
from .theme import application_font, load_stylesheet
from .ui.main_window import MainWindow


def main() -> int:
    data_dir = user_data_dir()
    log_file = setup_logging(data_dir / "logs")
    log = logging.getLogger("main")
    log.info("starting Voice Sales Agent, data dir: %s", data_dir)

    settings = AppSettings.load()
    settings.save()  # materialize default settings.json on first run

    storage = Storage(data_dir / "agent.db")
    seeded = seed_if_empty(storage)
    if seeded:
        log.info("demo dataset created")

    app = QApplication(sys.argv)
    app.setApplicationName("Voice Sales Agent")
    app.setApplicationDisplayName("Voice Sales Agent")
    app.setFont(application_font())
    app.setStyleSheet(load_stylesheet())

    engine_thread = EngineThread(settings, storage, data_dir)
    window = MainWindow(settings, storage, engine_thread=engine_thread)
    window.show()

    engine_thread.start()

    def _shutdown():
        engine_thread.graceful_stop()
        storage.close()

    app.aboutToQuit.connect(_shutdown)
    log.info("UI ready, log file: %s", log_file)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
