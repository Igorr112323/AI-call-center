"""Interactive text call simulator window (750x550, Dark Slate).

Left: terminal transcript + typed input + hangup. Right: live state-machine
inspector with recognized slots, catalog-tool badges and RTT log. Every turn
runs the real CallEngine/DialogManager inside the engine thread.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.audio import format_timecode
from ..telephony.text_simulator import TextSimulatorAgent
from ..theme import PALETTE
from . import icons
from .widgets import Badge, Card, SectionLabel

_STATE_LABELS = {
    "greeting": "GREETING",
    "collecting": "COLLECTING",
    "quoting": "QUOTING",
    "objection": "OBJECTION",
    "confirming": "CONFIRMING",
    "order_created": "DONE",
    "closed": "CLOSED",
}

_SLOT_LABELS = {
    "category": "Категория",
    "voltage": "Напряжение",
    "stroke_mm": "Ход штока",
    "force_n": "Усилие",
    "quantity": "Количество",
    "delivery_city": "Город доставки",
}

_SLOT_UNITS = {
    "voltage": "В", "stroke_mm": "мм", "force_n": "Н", "quantity": "шт",
}

_STATUS_RU = {"order": "заказ", "consultation": "консультация", "failed": "срыв"}

_QUICK_PHRASES = [
    "Нужен актуатор на 24 вольта, ход 300 мм, одна штука",
    "Это дорого, а скидку дадите?",
    "Да, оформляем заказ",
]


class TextCallWindow(QFrame):
    """Frameless-ish workspace window shown as a modal dialog wrapper."""

    closed = Signal(dict)
    _future_done = Signal(object)

    def __init__(self, engine_thread, settings, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._engine_thread = engine_thread
        self._settings = settings
        self.agent = TextSimulatorAgent(engine_thread.engine, settings)
        self._clock_seconds = 0
        self.setWindowTitle("Инспектор диалога / Текстовый симулятор")
        self.resize(750, 550)
        self.setMinimumSize(680, 480)
        self._future_done.connect(self._consume_future)
        self._pending: dict = {}
        self._build_ui()
        self._start_clock()
        self._submit(self.agent.start(), kind="start")

    # ------------------------------------------------------------------ layout
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        root.addWidget(self._build_chat(), 1)
        root.addWidget(self._build_inspector())

    def _build_chat(self) -> QWidget:
        card = Card()
        card.layout().setContentsMargins(12, 10, 12, 12)

        head = QHBoxLayout()
        title = QLabel("Текстовый симулятор звонка")
        title.setProperty("role", "screenTitle")
        self.clock = QLabel("00:00")
        self.clock.setProperty("role", "timecode")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.clock)
        card.add_layout(head)

        self.transcript = QScrollArea()
        self.transcript.setWidgetResizable(True)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._transcript_host = QWidget()
        self._transcript_box = QVBoxLayout(self._transcript_host)
        self._transcript_box.setContentsMargins(2, 4, 6, 4)
        self._transcript_box.setSpacing(12)
        self._transcript_box.addStretch(1)
        self.transcript.setWidget(self._transcript_host)
        self.transcript.verticalScrollBar().rangeChanged.connect(
            lambda _lo, hi: self.transcript.verticalScrollBar().setValue(hi))
        card.add_widget(self.transcript, 1)

        self._chips = []
        for phrase in _QUICK_PHRASES:
            chip = QPushButton(phrase)
            chip.setObjectName("Chip")
            chip.clicked.connect(lambda _=False, p=phrase: self._send(p))
            self._chips.append(chip)
            row = QHBoxLayout()
            row.addWidget(chip)
            row.addStretch(1)
            card.add_layout(row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Введите фразу клиента (например: «Нужен актуатор 24В 300мм») и нажмите Enter"
        )
        self.input.returnPressed.connect(self._on_send_clicked)
        self.send_btn = QPushButton("  Отправить")
        self.send_btn.setProperty("variant", "primary")
        self.send_btn.setIcon(icons.icon("send", "#062018"))
        self.send_btn.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn)
        card.add_layout(input_row)

        controls = QHBoxLayout()
        self.mute_box = QCheckBox("Озвучивать ответы голосом (TTS)")
        self.mute_box.setChecked(not self.agent.muted)
        self.mute_box.toggled.connect(self._on_mute_toggled)
        if not self.agent.audio_available:
            self.mute_box.setChecked(False)
            self.mute_box.setEnabled(False)
            self.mute_box.setText("TTS недоступен в этой среде")
        self.hangup_btn = QPushButton("  Положить трубку")
        self.hangup_btn.setProperty("variant", "danger")
        self.hangup_btn.setIcon(icons.icon("x", PALETTE["danger"]))
        self.hangup_btn.setFixedHeight(28)
        self.hangup_btn.clicked.connect(self._on_hangup)
        self.close_btn = QPushButton("  Закрыть")
        self.close_btn.setIcon(icons.icon("x"))
        self.close_btn.setFixedHeight(28)
        self.close_btn.setHidden(True)
        self.close_btn.clicked.connect(self.close)
        controls.addWidget(self.mute_box)
        controls.addStretch(1)
        controls.addWidget(self.hangup_btn)
        controls.addWidget(self.close_btn)
        card.add_layout(controls)
        return card

    def _build_inspector(self) -> QWidget:
        wrap = QWidget()
        wrap.setFixedWidth(250)
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        state_card = Card()
        state_card.add(SectionLabel("Состояние"))
        self.state_badge = Badge("GREETING", "ok")
        state_card.add(self.state_badge)
        box.addWidget(state_card)

        slots_card = Card()
        slots_card.add(SectionLabel("Распознанные параметры"))
        self.slots_table = QTableWidget(0, 2)
        self.slots_table.verticalHeader().setVisible(False)
        self.slots_table.horizontalHeader().setVisible(False)
        self.slots_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.slots_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.slots_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slots_table.horizontalHeader().setStretchLastSection(True)
        self.slots_table.setColumnWidth(0, 118)
        slots_card.add(self.slots_table)
        box.addWidget(slots_card, 1)

        tool_card = Card()
        tool_card.add(SectionLabel("Вызовы инструментов"))
        self.tool_host = QVBoxLayout()
        self.tool_host.setSpacing(6)
        tool_card.add_layout(self.tool_host)
        box.addWidget(tool_card)

        rtt_card = Card()
        rtt_card.add(SectionLabel("Время ответа"))
        self.rtt_label = QLabel("RTT: —")
        self.rtt_label.setProperty("role", "mono")
        rtt_card.add(self.rtt_label)
        box.addWidget(rtt_card)
        return wrap

    # ------------------------------------------------------------------ runtime
    def _start_clock(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._clock_seconds += 1
        self.clock.setText(format_timecode(self._clock_seconds))

    def _on_mute_toggled(self, checked: bool) -> None:
        self.agent.set_muted(not checked)

    def _on_send_clicked(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._send(text)

    def _send(self, text: str) -> None:
        self._append_line("ВЫ", text, "client")
        self._submit(self.agent.send_user_utterance(text), kind="turn")

    def _on_hangup(self) -> None:
        self.hangup_btn.setEnabled(False)
        self.input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._submit(self.agent.hangup(), kind="hangup")

    def _render_completed(self, result: dict) -> None:
        self._timer.stop()
        self.mute_box.setEnabled(False)
        for chip in self._chips:
            chip.setEnabled(False)
        self.hangup_btn.setHidden(True)
        self.close_btn.setHidden(False)
        self.state_badge.setText("ЗАВЕРШЁН")
        status_ru = _STATUS_RU.get(result.get("status", ""), result.get("status", ""))
        if result.get("pdf"):
            note = (f"Звонок завершён · статус «{status_ru}» · "
                    f"заказ оформлен, PDF КП сформирован")
        else:
            note = f"Звонок завершён · статус «{status_ru}»"
        self._append_line("СИСТЕМА", note, "system")

    def closeEvent(self, event) -> None:
        # Window-close acts as hangup so no engine session is orphaned.
        if self.agent.active:
            event.ignore()
            self._on_hangup()
            return
        event.accept()

    # --------------------------------------------------------- engine bridging
    def _submit(self, coro, kind: str) -> None:
        future = self._engine_thread.submit(coro)
        self._pending[future] = kind
        future.add_done_callback(lambda f: self._future_done.emit(f))

    def _consume_future(self, future) -> None:
        kind = self._pending.pop(future, "turn")
        try:
            payload = future.result()
        except Exception as exc:  # noqa: BLE001 — surface engine errors in-window
            self._append_line("СИСТЕМА", f"Ошибка: {exc}", "system")
            return
        if kind == "start":
            self._append_line("AI-ПРОДАВЕЦ", payload["greeting"], "agent")
            self.state_badge.setText(_STATE_LABELS.get(payload["state"], payload["state"]))
        elif kind == "turn":
            self._render_turn(payload)
        elif kind == "hangup":
            self._render_completed(payload)
            self.closed.emit(payload)

    def _render_turn(self, event) -> None:
        self._append_line("AI-ПРОДАВЕЦ", event.reply, "agent")
        self.state_badge.setText(_STATE_LABELS.get(event.state, event.state))
        self.rtt_label.setText(f"RTT: {event.rtt_ms} ms")
        self._reload_slots(event.slots, event.product)
        if "check_catalog_and_price" in event.tool_names and event.product:
            self._append_tool_badge(event)

    def _reload_slots(self, slots: dict, product: dict | None) -> None:
        rows = [(k, v) for k, v in slots.items() if k in _SLOT_LABELS]
        if product and product.get("found"):
            rows.append(("__price__", product["price"]))
        self.slots_table.setRowCount(len(rows))
        for row, (key, value) in enumerate(rows):
            if key == "__price__":
                label, rendered = "Цена", _money(value)
            else:
                unit = _SLOT_UNITS.get(key, "")
                rendered = f"{value} {unit}".strip()
                label = _SLOT_LABELS.get(key, key)
            name_item = QTableWidgetItem(label)
            name_item.setForeground(_muted())
            value_item = QTableWidgetItem(rendered)
            value_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.slots_table.setItem(row, 0, name_item)
            self.slots_table.setItem(row, 1, value_item)
            self.slots_table.setRowHeight(row, 28)

    def _append_tool_badge(self, event) -> None:
        slots = event.slots
        query = ", ".join(
            part for part in (
                f"{slots.get('voltage')}В" if slots.get("voltage") else "",
                f"{slots.get('stroke_mm')}мм" if slots.get("stroke_mm") else "",
            ) if part
        ) or "параметры"
        product = event.product
        badge = Badge(
            f"Запрос цены: {query} → Найдено: {product['article']} "
            f"({_money(product['price'])})",
            "",
        )
        badge.setWordWrap(True)
        badge.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(badge, 1)
        self.tool_host.addLayout(row)

    # ------------------------------------------------------------- transcript
    def _append_line(self, tag: str, text: str, kind: str) -> None:
        wrap = QWidget()
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(8)
        tag_label = QLabel(tag)
        tag_label.setProperty("role", "accent" if kind == "agent" else "fieldLabel")
        font = tag_label.font()
        font.setBold(True)
        tag_label.setFont(font)
        time_label = QLabel(QTime.currentTime().toString("HH:mm:ss"))
        time_label.setProperty("role", "timecode")
        header.addWidget(tag_label)
        header.addWidget(time_label)
        header.addStretch(1)
        box.addLayout(header)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(body)

        self._transcript_box.insertWidget(self._transcript_box.count() - 1, wrap)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())


def _money(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def _muted():
    return QColor(PALETTE["text_muted"])
