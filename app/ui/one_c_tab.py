"""Tab 3 — 1C:Enterprise connection, health probe and field mapping."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import AppSettings
from ..integrations.one_c_client import OneCClient, OneCError
from . import icons
from .widgets import Badge, Card, IconButton, IntEdit, LabeledField, SectionLabel

_MAPPING_ROWS = [
    ("article", "Артикул"),
    ("name", "Наименование"),
    ("stock", "Остаток"),
    ("price", "Цена"),
    ("warehouse", "Склад"),
]


class OneCTab(QWidget):
    saved = Signal()

    def __init__(self, settings: AppSettings, runner=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._runner = runner  # callable: coro -> concurrent.futures.Future

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Подключение к 1С")
        title.setProperty("role", "screenTitle")
        header.addWidget(title)
        header.addStretch(1)
        save = QPushButton("  Сохранить")
        save.setProperty("variant", "primary")
        save.setIcon(icons.icon("check", "#062018"))
        save.clicked.connect(self._save)
        header.addWidget(save)
        root.addLayout(header)

        conn_card = Card()
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.mode = QComboBox()
        self.mode.addItems(["http_service", "odata"])
        self.mode.currentTextChanged.connect(self._mode_changed)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("http://192.168.1.100/trade_base/hs/ai_sales/")
        self.odata_path = QLineEdit()
        self.odata_path.setPlaceholderText("/odata/standard.odata")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.timeout = IntEdit(1500, 300, 5000, suffix=" мс")
        self.retries = IntEdit(1, 0, 4)

        form.addRow("Режим", self.mode)
        form.addRow("URL базы / HTTP-сервиса", self.base_url)
        form.addRow("Путь OData", self.odata_path)
        form.addRow("Технический пользователь", self.username)
        form.addRow("Пароль", self.password)
        form.addRow("Таймаут запроса", self.timeout)
        form.addRow("Повторов при сбое", self.retries)
        conn_card.layout().addLayout(form)

        probe_row = QHBoxLayout()
        self.test_btn = QPushButton("  Проверить связь с 1С")
        self.test_btn.setIcon(icons.icon("zap"))
        self.test_btn.clicked.connect(self._run_probe)
        self.probe_badge = Badge("Не проверено")
        probe_row.addWidget(self.test_btn)
        probe_row.addWidget(self.probe_badge)
        probe_row.addStretch(1)
        conn_card.layout().addLayout(probe_row)
        root.addWidget(conn_card)

        mapping_card = Card()
        mapping_head = QHBoxLayout()
        mapping_head.addWidget(SectionLabel("Маппинг полей OData"))
        mapping_head.addStretch(1)
        mapping_card.layout().addLayout(mapping_head)

        self.mapping_table = QTableWidget(len(_MAPPING_ROWS), 2)
        self.mapping_table.setHorizontalHeaderLabels(["Поле агента", "Имя реквизита 1С"])
        self.mapping_table.verticalHeader().setVisible(False)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mapping_table.setColumnWidth(0, 180)
        for row, (key, label) in enumerate(_MAPPING_ROWS):
            key_item = QTableWidgetItem(label)
            key_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.mapping_table.setItem(row, 0, key_item)
            self.mapping_table.setRowHeight(row, 30)
        mapping_card.add(self.mapping_table)
        root.addWidget(mapping_card, 1)

        self.load_from(settings)

    def load_from(self, settings: AppSettings) -> None:
        c = settings.one_c
        self.mode.setCurrentText(c.mode)
        self.base_url.setText(c.base_url)
        self.odata_path.setText(c.odata_path)
        self.username.setText(c.username)
        self.password.setText(c.password)
        self.timeout.setValue(c.timeout_ms)
        self.retries.setValue(c.retries)
        for row, (key, _label) in enumerate(_MAPPING_ROWS):
            self.mapping_table.setItem(row, 1, QTableWidgetItem(c.mapping.get(key, "")))
        self._mode_changed(c.mode)

    def _mode_changed(self, mode: str) -> None:
        is_odata = mode == "odata"
        self.odata_path.setEnabled(is_odata)
        self.mapping_table.setEnabled(is_odata)

    def _collect(self) -> None:
        c = self.settings.one_c
        c.mode = self.mode.currentText()
        c.base_url = self.base_url.text().strip()
        c.odata_path = self.odata_path.text().strip()
        c.username = self.username.text().strip()
        c.password = self.password.text()
        c.timeout_ms = self.timeout.value()
        c.retries = self.retries.value()
        c.mapping = {
            key: (self.mapping_table.item(row, 1).text().strip()
                  if self.mapping_table.item(row, 1) else "")
            for row, (key, _label) in enumerate(_MAPPING_ROWS)
        }

    def _save(self) -> None:
        self._collect()
        self.settings.save()
        self.saved.emit()

    def _run_probe(self) -> None:
        self._collect()
        self.probe_badge.setText("Проверка…")
        self.probe_badge.set_tone("warn")
        self.test_btn.setEnabled(False)

        async def ping():
            client = OneCClient(self.settings.one_c)
            try:
                return await client.ping()
            finally:
                await client.aclose()

        if self._runner is None:
            self.probe_badge.setText("Служба не запущена")
            self.probe_badge.set_tone("danger")
            self.test_btn.setEnabled(True)
            return
        future = self._runner(ping())
        future.add_done_callback(self._probe_done)

    def _probe_done(self, future) -> None:
        self.test_btn.setEnabled(True)
        try:
            result = future.result()
        except (OneCError, Exception) as exc:  # noqa: BLE001 - surface any transport error
            self.probe_badge.setText(f"Нет связи: {str(exc)[:48]}")
            self.probe_badge.set_tone("danger")
            return
        warehouses = result.get("warehouses") or []
        text = f"Связь OK · складов: {len(warehouses)}" if warehouses else "Связь OK"
        self.probe_badge.setText(text)
        self.probe_badge.set_tone("ok")
