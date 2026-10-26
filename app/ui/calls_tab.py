"""Tab 1 — call registry (master) + call inspector (detail), 40/60 split."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.models import CallRecord, CallStatus
from ..core.storage import Storage
from ..theme import PALETTE
from . import icons
from .inspector import CallInspector, _format_phone, _money
from .widgets import SearchBox, StatusDot

_FILTERS: list[tuple[str, CallStatus | None]] = [
    ("Все", None),
    ("Заказы", CallStatus.ORDER),
    ("Консультации", CallStatus.CONSULTATION),
    ("Срывы", CallStatus.FAILED),
]


class CallsTab(QWidget):
    selection_changed = Signal(object)        # CallRecord | None
    post_to_1c = Signal(int)
    resend_offer = Signal(int)
    simulate_requested = Signal()

    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._records: list[CallRecord] = []
        self._filter: CallStatus | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_master())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([520, 780])
        root.addWidget(splitter)

        self.inspector.post_to_1c.connect(self.post_to_1c)
        self.inspector.resend_offer.connect(self.resend_offer)
        self.refresh()

    # ------------------------------------------------------------------ master
    def _build_master(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 14, 12, 14)
        box.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("Реестр звонков")
        title.setProperty("role", "screenTitle")
        self.count_label = QLabel()
        self.count_label.setProperty("role", "mono")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.count_label)
        box.addLayout(title_row)

        self.search = SearchBox()
        self.search.text_changed.connect(lambda _: self.refresh())
        box.addWidget(self.search)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        for idx, (label, status) in enumerate(_FILTERS):
            chip = QPushButton(label)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setChecked(idx == 0)
            chip.clicked.connect(lambda _=False, s=status: self._set_filter(s))
            self._chip_group.addButton(chip)
            chip_row.addWidget(chip)
        chip_row.addStretch(1)
        demo = QPushButton("  Демо-диалог")
        demo.setProperty("variant", "ghost")
        demo.setIcon(icons.icon("messages"))
        demo.clicked.connect(self.simulate_requested.emit)
        chip_row.addWidget(demo)
        box.addLayout(chip_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Клиент", "Суть", "Сумма"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 26)
        self.table.setColumnWidth(1, 178)
        self.table.setColumnWidth(3, 108)
        self.table.itemSelectionChanged.connect(self._on_selection)
        box.addWidget(self.table, 1)
        return panel

    # ------------------------------------------------------------------ detail
    def _build_detail(self) -> QWidget:
        self.inspector = CallInspector()
        return self.inspector

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        search = self.search.text().strip()
        self._records = self.storage.list_calls(search=search, status=self._filter)
        self.count_label.setText(f"{len(self._records)} записей")
        self.table.setRowCount(len(self._records))
        for row, call in enumerate(self._records):
            self.table.setRowHeight(row, 32)

            dot = StatusDot(_status_key(call))
            dot_cell = QTableWidgetItem()
            dot_cell.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, dot_cell)
            self.table.setCellWidget(row, 0, dot)

            client = QTableWidgetItem(
                f"{_format_phone(call.phone)}\n{call.started_at:%d.%m.%Y %H:%M}"
            )
            client.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, client)

            summary = QTableWidgetItem(call.summary or call.status.label_ru)
            summary.setForeground(_muted_brush())
            summary.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 2, summary)

            amount_text = _money(call.order.total) if call.order else "—"
            amount = QTableWidgetItem(amount_text)
            amount.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            amount.setData(Qt.ItemDataRole.FontRole, _mono_font())
            amount.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 3, amount)

        if self._records:
            self.table.selectRow(0)
        else:
            self.inspector.bind(None)
            self.selection_changed.emit(None)

    def _set_filter(self, status: CallStatus | None) -> None:
        self._filter = status
        self.refresh()

    def _on_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        call = self._records[rows[0].row()]
        self.inspector.bind(call)
        self.selection_changed.emit(call)

    def current_call(self) -> CallRecord | None:
        rows = self.table.selectionModel().selectedRows()
        return self._records[rows[0].row()] if rows else None

    def mark_order_posted(self, order_id: int, number_1c: str) -> None:
        self.inspector.order_card.mark_posted(number_1c)


def _status_key(call: CallRecord) -> str:
    return {
        CallStatus.ORDER: "order",
        CallStatus.CONSULTATION: "consultation",
        CallStatus.FAILED: "failed",
        CallStatus.ONGOING: "ongoing",
    }[call.status]


def _muted_brush():
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(PALETTE["text_muted"]))


def _mono_font():
    from PySide6.QtGui import QFont
    font = QFont()
    font.setFamilies(["Cascadia Code", "Cascadia Mono", "Consolas"])
    font.setPointSize(9)
    return font
