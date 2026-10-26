"""Top-level shell: icon sidebar nav + stacked workspaces + status bar."""
from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_DISPLAY_NAME, __version__
from ..config import AppSettings
from ..core.storage import Storage
from ..theme import PALETTE
from . import icons
from .call_bar import CallControlBar
from .calls_tab import CallsTab
from .catalog_tab import CatalogTab
from .scenario_tab import ScenarioTab
from .telephony_tab import TelephonyTab
from .widgets import ScrollArea, StatusDot

log = logging.getLogger(__name__)

_DEMO_PHRASES = [
    "Здравствуйте, нужен актуатор на двадцать четыре вольта, ход штока триста миллиметров.",
    "Одна штука, доставка в Краснодар.",
    "Хорошо, давайте оформим заказ.",
]


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, storage: Storage, engine_thread=None):
        super().__init__()
        self.settings = settings
        self.storage = storage
        self.engine_thread = engine_thread

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(1120, 700)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        workspace = QWidget()
        workspace_box = QVBoxLayout(workspace)
        workspace_box.setContentsMargins(0, 0, 0, 0)
        workspace_box.setSpacing(0)
        self.call_bar = CallControlBar()
        self.call_bar.start_local_requested.connect(self._start_local_call)
        self.call_bar.stop_requested.connect(self._stop_local_call)
        workspace_box.addWidget(self.call_bar)
        workspace_box.addWidget(self._build_pages(), 1)
        root.addWidget(workspace, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Готов к работе")
        if engine_thread is not None:
            engine_thread.event_received.connect(self._on_engine_event)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(208)
        box = QVBoxLayout(sidebar)
        box.setContentsMargins(0, 16, 0, 14)
        box.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 16, 18)
        mark = QLabel()
        mark.setPixmap(icons.icon("mic", PALETTE["accent"], 18).pixmap(18, 18))
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel("VOICE SALES")
        title.setObjectName("BrandTitle")
        subtitle = QLabel(f"AI AGENT · v{__version__}")
        subtitle.setObjectName("BrandSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        brand.addWidget(mark)
        brand.addSpacing(10)
        brand.addLayout(title_block)
        box.addLayout(brand)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        nav_specs = [
            ("phone", "Реестр звонков"),
            ("messages", "Сценарий и промпт"),
            ("database", "Источник данных"),
            ("tower", "Телефония и голос"),
        ]
        for idx, (icon_name, label) in enumerate(nav_specs):
            btn = QPushButton(f"  {label}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setIcon(icons.icon(icon_name))
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self._switch_page(i))
            self.nav_group.addButton(btn)
            self._nav_buttons.append(btn)
            box.addWidget(btn)
        self._nav_buttons[0].setChecked(True)

        box.addStretch(1)

        sip_state = QHBoxLayout()
        sip_state.setContentsMargins(16, 0, 16, 0)
        sip_state.setSpacing(8)
        self.sip_dot = StatusDot("failed")
        self.sip_label = QLabel("SIP не настроен")
        self.sip_label.setProperty("role", "muted")
        sip_state.addWidget(self.sip_dot)
        sip_state.addWidget(self.sip_label)
        box.addLayout(sip_state)
        return sidebar

    # ------------------------------------------------------------------ pages
    def _build_pages(self) -> QWidget:
        self.stack = QStackedWidget()

        runner = self._runner
        self.calls_tab = CallsTab(self.storage)
        self.scenario_tab = ScenarioTab(self.settings)
        self.catalog_tab = CatalogTab(self.settings, self.storage, runner=runner)
        self.telephony_tab = TelephonyTab(self.settings)

        self.stack.addWidget(self.calls_tab)
        self.stack.addWidget(ScrollArea(self.scenario_tab))
        self.stack.addWidget(ScrollArea(self.catalog_tab))
        self.stack.addWidget(ScrollArea(self.telephony_tab))

        self.calls_tab.post_to_1c.connect(self._post_order)
        self.calls_tab.resend_offer.connect(self._resend_offer)
        self.calls_tab.simulate_requested.connect(self._run_demo_call)
        self.scenario_tab.saved.connect(lambda: self.statusBar().showMessage("Сценарий сохранён", 4000))
        self.catalog_tab.saved.connect(lambda: self.statusBar().showMessage("Источник данных сохранён", 4000))
        self.telephony_tab.saved.connect(lambda: self.statusBar().showMessage("Настройки телефонии сохранены", 4000))
        return self.stack

    def _switch_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._nav_buttons[index].setChecked(True)

    # ------------------------------------------------------------ engine glue
    def _runner(self, coro):
        if self.engine_thread is None or self.engine_thread.loop is None:
            self.statusBar().showMessage("Служба ещё не запущена", 4000)
            return None
        return self.engine_thread.submit(coro)

    def _on_engine_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind in ("call_started", "call_ended", "order_saved", "order_posted", "offer_resent"):
            self.calls_tab.refresh()
        if kind == "call_started":
            self.call_bar.call_started(event.get("mode", "demo"))
        elif kind == "call_ended":
            self.call_bar.call_ended()
        elif kind == "mic_level":
            self.call_bar.update_level(float(event.get("db", -50)))
        if kind == "engine_ready":
            registered = event.get("sip_registered", False)
            self._set_sip_state(registered)
            engine = self._engine()
            if engine is not None:
                self.call_bar.set_audio_available(engine.local_audio_available)
        elif kind == "sip":
            self._set_sip_state(event.get("kind") == "registered", event.get("detail", ""))
        elif kind == "voice" and event.get("ms"):
            self.statusBar().showMessage(f"Задержка ответа: {event['ms']} мс", 5000)

    def _start_local_call(self) -> None:
        engine = self._engine()
        if not engine or not engine.local_audio_available:
            self.statusBar().showMessage("Микрофон недоступен в этой среде", 4000)
            return
        future = self.engine_thread.submit(engine.start_call("+7 (микрофон)", mode="local"))
        future.add_done_callback(
            lambda f: self._future_status(f, "Тестовый звонок запущен"))

    def _stop_local_call(self) -> None:
        engine = self._engine()
        if engine:
            future = self.engine_thread.submit(engine.end_call())
            future.add_done_callback(
                lambda f: self._future_status(f, "Звонок завершён и записан в реестр"))

    def _set_sip_state(self, registered: bool, detail: str = "") -> None:
        self.sip_dot.set_status("order" if registered else "failed")
        self.sip_label.setText("SIP в сети" if registered else "SIP не зарегистрирован")
        self.telephony_tab.set_registration_state(registered, detail)

    def _post_order(self, order_id: int) -> None:
        engine = self._engine()
        if not engine:
            return
        future = self.engine_thread.submit(engine.post_order_to_1c(order_id))
        future.add_done_callback(lambda f: self._future_status(f, "Документ проведён в 1С"))

    def _resend_offer(self, order_id: int) -> None:
        engine = self._engine()
        if not engine:
            return
        future = self.engine_thread.submit(engine.resend_offer(order_id))
        future.add_done_callback(lambda f: self._future_status(f, "КП отправлено повторно"))

    def _future_status(self, future, ok_text: str) -> None:
        try:
            future.result()
            self.statusBar().showMessage(ok_text, 5000)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Ошибка операции: {exc}", 6000)

    def _run_demo_call(self) -> None:
        engine = self._engine()
        if not engine:
            self.statusBar().showMessage("Служба ещё не запущена", 4000)
            return

        async def demo():
            call_id = await engine.start_call("+79182041122")
            for phrase in _DEMO_PHRASES:
                await engine.simulate_utterance(phrase)
                await asyncio.sleep(0.1)
            await engine.end_call()
            return call_id

        future = self.engine_thread.submit(demo())
        future.add_done_callback(lambda f: self._future_status(f, "Тестовый звонок завершён"))

    def _engine(self):
        if self.engine_thread is None:
            return None
        return getattr(self.engine_thread, "engine", None)

    def closeEvent(self, event) -> None:
        if self.engine_thread is not None:
            self.engine_thread.graceful_stop()
        event.accept()
