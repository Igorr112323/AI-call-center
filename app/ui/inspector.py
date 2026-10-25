"""Call inspector: transcript terminal + collapsible 1C order card."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core.audio import format_timecode
from ..core.models import CallRecord, CallStatus
from ..theme import PALETTE
from . import icons
from .player import PlayerBar
from .widgets import Badge, SectionLabel, hline


class TranscriptView(QScrollArea):
    """Terminal-style role-tagged dialogue, no bubbles, no avatars.

    Layout-driven rows size themselves from wrapped text, so long phrases are
    never clipped the way fixed-height QListWidget rows would be.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._box = QVBoxLayout(self._container)
        self._box.setContentsMargins(2, 4, 8, 4)
        self._box.setSpacing(14)
        self._box.addStretch(1)
        self.setWidget(self._container)

    def bind(self, call: CallRecord) -> None:
        while self._box.count() > 1:
            item = self._box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for line in call.transcript:
            self._box.insertWidget(self._box.count() - 1,
                                   self._row_widget(line.speaker.value, line.text, line.started_at))

    def _row_widget(self, speaker: str, text: str, at: float) -> QWidget:
        wrap = QWidget()
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(8)
        tag = QLabel("AI" if speaker == "agent" else "КЛИЕНТ")
        tag.setProperty("role", "accent" if speaker == "agent" else "fieldLabel")
        tag_font = tag.font()
        tag_font.setBold(True)
        tag.setFont(tag_font)
        timecode = QLabel(format_timecode(at))
        timecode.setProperty("role", "timecode")
        header.addWidget(tag)
        header.addWidget(timecode)
        header.addStretch(1)
        box.addLayout(header)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet("color:#E6E8EC;")
        box.addWidget(body)
        return wrap


class OrderCard(QFrame):
    post_to_1c = Signal(int)
    resend_offer = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._order_id: int | None = None

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 10, 14, 12)
        box.setSpacing(8)

        head = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setObjectName("CollapseButton")
        self.toggle.setText("ЗАКАЗ ИЗ 1С")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.toggle.clicked.connect(self._on_toggle)
        self._chevron = icons.icon("chevron-down", PALETTE["text_muted"], 14)
        self.toggle.setIcon(self._chevron)
        self.sync_badge = Badge("Не синхронизировано", "warn")

        head.addWidget(self.toggle)
        head.addStretch(1)
        head.addWidget(self.sync_badge)
        box.addLayout(head)

        self.body = QWidget()
        body_box = QVBoxLayout(self.body)
        body_box.setContentsMargins(0, 0, 0, 0)
        body_box.setSpacing(8)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Артикул", "Наименование", "Кол-во", "Цена", "Сумма"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(118)
        body_box.addWidget(self.table)

        self.total = QLabel()
        self.total.setProperty("role", "amount")
        self.total.setAlignment(Qt.AlignmentFlag.AlignRight)
        body_box.addWidget(self.total)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.resend_btn = QPushButton("  Отправить КП повторно")
        self.resend_btn.setIcon(icons.icon("send"))
        self.resend_btn.clicked.connect(lambda: self._emit(self.resend_offer))
        self.post_btn = QPushButton("  Провести документ в 1С")
        self.post_btn.setProperty("variant", "primary")
        self.post_btn.setIcon(icons.icon("database", PALETTE["canvas"]))
        self.post_btn.clicked.connect(lambda: self._emit(self.post_to_1c))
        actions.addStretch(1)
        actions.addWidget(self.resend_btn)
        actions.addWidget(self.post_btn)
        body_box.addLayout(actions)

        box.addWidget(self.body)
        self.setHidden(True)

    def _emit(self, signal) -> None:
        if self._order_id is not None:
            signal.emit(self._order_id)

    def bind(self, call: CallRecord) -> None:
        order = call.order
        if order is None or not order.items:
            self.setHidden(True)
            self._order_id = None
            return
        self.setHidden(False)
        self._order_id = order.id
        self.table.setRowCount(len(order.items))
        for row, item in enumerate(order.items):
            values = [item.article, item.name, _num(item.quantity),
                      _money(item.price), _money(item.sum)]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col in (2, 3, 4):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    cell.setFont(_mono())
                self.table.setItem(row, col, cell)
            self.table.setRowHeight(row, 30)
        self.total.setText(f"Итого: {_money(order.total)}")

        if order.posted and order.number_1c:
            self.sync_badge.setText(f"Проведено в 1С · {order.number_1c}")
            self.sync_badge.set_tone("ok")
            self.post_btn.setEnabled(False)
        else:
            self.sync_badge.setText("Черновик — не проведено")
            self.sync_badge.set_tone("warn")
            self.post_btn.setEnabled(True)

    def mark_posted(self, number_1c: str) -> None:
        self.sync_badge.setText(f"Проведено в 1С · {number_1c}")
        self.sync_badge.set_tone("ok")
        self.post_btn.setEnabled(False)

    def _on_toggle(self) -> None:
        collapsed = not self.toggle.isChecked()
        self.body.setHidden(collapsed)
        self.toggle.setIcon(
            icons.icon("chevron-right" if collapsed else "chevron-down",
                       PALETTE["text_muted"], 14)
        )


class CallInspector(QWidget):
    post_to_1c = Signal(int)
    resend_offer = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        header = QHBoxLayout()
        self.title = QLabel("Выберите звонок")
        self.title.setProperty("role", "screenTitle")
        self.status_badge = Badge()
        self.status_badge.setHidden(True)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.status_badge)
        box.addLayout(header)

        self.meta = QLabel("")
        self.meta.setProperty("role", "muted")
        box.addWidget(self.meta)

        self.player = PlayerBar()
        box.addWidget(self.player)
        box.addWidget(hline())

        box.addWidget(SectionLabel("Расшифровка диалога"))
        self.transcript = TranscriptView()
        self.transcript.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        box.addWidget(self.transcript, 1)

        self.order_card = OrderCard()
        self.order_card.post_to_1c.connect(self.post_to_1c)
        self.order_card.resend_offer.connect(self.resend_offer)
        box.addWidget(self.order_card)

    def bind(self, call: CallRecord | None) -> None:
        self.player.release()
        if call is None:
            self.title.setText("Выберите звонок")
            self.meta.setText("")
            self.status_badge.setHidden(True)
            self.transcript.clear()
            self.order_card.setHidden(True)
            return
        self.title.setText(_format_phone(call.phone))
        self.meta.setText(
            f"{call.started_at:%d.%m.%Y %H:%M}  ·  {format_timecode(call.duration_sec)}"
            + (f"  ·  {call.summary}" if call.summary else "")
        )
        self.status_badge.setText(call.status.label_ru)
        self.status_badge.setHidden(False)
        self.status_badge.set_tone(_badge_tone(call.status))
        self.transcript.bind(call)
        self.order_card.bind(call)
        self.player.load(call.record_path)


def _money(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def _num(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _mono():
    from PySide6.QtGui import QFont
    font = QFont("Cascadia Code")
    font.setFamilies(["Cascadia Code", "Cascadia Mono", "Consolas"])
    font.setPointSize(9)
    return font


def _badge_tone(status: CallStatus) -> str:
    return {
        CallStatus.ORDER: "ok",
        CallStatus.CONSULTATION: "",
        CallStatus.FAILED: "danger",
        CallStatus.ONGOING: "warn",
    }[status]


def _format_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone
