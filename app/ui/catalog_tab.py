"""Data source workspace: standalone Excel/CSV catalog vs live 1C server."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import AppSettings
from ..integrations.catalog_provider import LocalCatalogProvider
from ..integrations.one_c_client import OneCClient, OneCError
from ..integrations.price_parser import PriceListError
from ..theme import PALETTE
from . import icons
from .widgets import Badge, Card, IntEdit, SearchBox, SectionLabel

_MAPPING_ROWS = [
    ("article", "Артикул"),
    ("name", "Наименование"),
    ("stock", "Остаток"),
    ("price", "Цена"),
    ("warehouse", "Склад"),
]
_FILE_FILTER = "Прайс-листы (*.xlsx *.xls *.csv)"


class DropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(96)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        icon = QLabel()
        icon.setPixmap(icons.icon("package", PALETTE["text_muted"], 20).pixmap(20, 20))
        title = QLabel("Перетащите сюда .xlsx / .csv или выберите файл")
        title.setProperty("role", "fieldLabel")
        hint = QLabel("Колонки определяются автоматически: артикул, наименование, цена, остаток")
        hint.setProperty("role", "muted")
        box.addWidget(icon)
        box.addWidget(title)
        box.addWidget(hint)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите прайс-лист", "", _FILE_FILTER)
        if path:
            self.file_dropped.emit(path)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


class CatalogTab(QWidget):
    saved = Signal()

    def __init__(self, settings: AppSettings, storage, runner=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.storage = storage
        self._runner = runner
        self.provider = LocalCatalogProvider(storage, settings.catalog)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Источник данных и каталог")
        title.setProperty("role", "screenTitle")
        header.addWidget(title)
        header.addStretch(1)
        save = QPushButton("  Сохранить")
        save.setProperty("variant", "primary")
        save.setIcon(icons.icon("check", "#062018"))
        save.clicked.connect(self._save)
        header.addWidget(save)
        root.addLayout(header)

        root.addWidget(self._build_segmented())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_local_panel())
        self.stack.addWidget(self._build_one_c_panel())
        root.addWidget(self.stack, 1)

        self._load_state()

    # ------------------------------------------------------------- segmented
    def _build_segmented(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("SegmentedBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.segment_group = QButtonGroup(self)
        self.segment_group.setExclusive(True)
        self.btn_local = QPushButton("Автономный прайс-лист (Excel/CSV)")
        self.btn_one_c = QPushButton("Сервер 1С:Предприятие")
        for btn in (self.btn_local, self.btn_one_c):
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)
            self.segment_group.addButton(btn)
        layout.addStretch(1)
        self.btn_local.clicked.connect(lambda: self._switch(0))
        self.btn_one_c.clicked.connect(lambda: self._switch(1))
        return bar

    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    # ----------------------------------------------------------- local panel
    def _build_local_panel(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)

        import_card = Card()
        import_head = QHBoxLayout()
        import_head.addWidget(SectionLabel("Прайс-лист"))
        import_head.addStretch(1)
        self.import_badge = Badge("0 товаров")
        import_head.addWidget(self.import_badge)
        import_card.layout().addLayout(import_head)

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self._import_file)
        import_card.add(self.drop_zone)

        btn_row = QHBoxLayout()
        pick = QPushButton("  Загрузить прайс-лист (.xlsx / .csv)")
        pick.setIcon(icons.icon("file"))
        pick.clicked.connect(self._pick_file)
        self.import_status = QLabel("")
        self.import_status.setProperty("role", "muted")
        btn_row.addWidget(pick)
        btn_row.addWidget(self.import_status)
        btn_row.addStretch(1)
        import_card.layout().addLayout(btn_row)

        preset_row = QHBoxLayout()
        preset_caption = QLabel("Демо-каталог в один клик:")
        preset_caption.setProperty("role", "fieldLabel")
        preset_row.addWidget(preset_caption)
        self._preset_files = {
            "Актуаторы": "actuators_catalog.xlsx",
            "РВД": "rvd_hydraulics_catalog.xlsx",
            "Коробки": "corrugated_boxes_catalog.xlsx",
        }
        for label, filename in self._preset_files.items():
            preset_btn = QPushButton(f"  {label}")
            preset_btn.setObjectName("Chip")
            preset_btn.setIcon(icons.icon("package", PALETTE["text_muted"], 14))
            preset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            preset_btn.clicked.connect(
                lambda _=False, f=filename: self._load_preset(f))
            preset_row.addWidget(preset_btn)
        preset_row.addStretch(1)
        import_card.layout().addLayout(preset_row)
        box.addWidget(import_card)

        test_card = Card()
        test_card.add(SectionLabel("Быстрый тест поиска"))
        self.fuzzy_box = SearchBox("Например: актуатор 24В 300мм")
        self.fuzzy_box.text_changed.connect(self._fuzzy_test)
        test_card.add(self.fuzzy_box)
        self.fuzzy_result = Badge("Введите параметры для проверки")
        test_card.add(self.fuzzy_result)
        box.addWidget(test_card)

        preview_card = Card()
        preview_head = QHBoxLayout()
        preview_head.addWidget(SectionLabel("Предпросмотр (первые 50 позиций)"))
        preview_head.addStretch(1)
        self.preview_count = QLabel()
        self.preview_count.setProperty("role", "mono")
        preview_head.addWidget(self.preview_count)
        preview_card.layout().addLayout(preview_head)

        self.preview = QTableWidget(0, 5)
        self.preview.setHorizontalHeaderLabels(["Артикул", "Наименование", "Категория", "Цена", "Остаток"])
        self.preview.verticalHeader().setVisible(False)
        self.preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.preview.setColumnWidth(0, 130)
        self.preview.setColumnWidth(2, 130)
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        preview_card.add(self.preview)
        box.addWidget(preview_card, 1)
        return panel

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите прайс-лист", "", _FILE_FILTER)
        if path:
            self._import_file(path)

    def _load_preset(self, filename: str) -> None:
        from ..paths import preset_path
        self._import_file(str(preset_path(filename)))

    def _import_file(self, path: str) -> None:
        try:
            count = self.provider.import_file(path)
        except (PriceListError, OSError) as exc:
            self.import_status.setText(f"Ошибка импорта: {exc}")
            return
        self.settings.catalog.last_import_path = path
        self.import_status.setText(f"Загружен: {path}")
        self._reload_preview()

    def _reload_preview(self) -> None:
        count = self.provider.count()
        self.import_badge.setText(f"{count} товаров")
        rows = self.provider.preview(limit=50)
        self.preview.setRowCount(len(rows))
        for row, item in enumerate(rows):
            self.preview.setRowHeight(row, 30)
            values = [item["sku"], item["name"], item["category"],
                      f"{item['price']:,.0f} ₽".replace(",", " "), str(item["stock"])]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col in (3, 4):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.preview.setItem(row, col, cell)
        self.preview_count.setText(f"показано {len(rows)} из {count}")

    def _fuzzy_test(self, text: str) -> None:
        text = text.strip()
        if len(text) < 3:
            self.fuzzy_result.setText("Введите параметры для проверки")
            self.fuzzy_result.set_tone("")
            return
        product = self.provider.lookup_product(text)
        if not product.found:
            self.fuzzy_result.setText("Совпадений не найдено")
            self.fuzzy_result.set_tone("danger")
            self.preview.clearSelection()
            return
        self.fuzzy_result.setText(
            f"{product.article} · {product.name} · {product.price:,.0f} ₽ · остаток {int(product.stock)}".replace(",", " ")
        )
        self.fuzzy_result.set_tone("ok")
        for row in range(self.preview.rowCount()):
            if self.preview.item(row, 0).text() == product.article:
                self.preview.selectRow(row)
                break

    # ------------------------------------------------------------- 1C panel
    def _build_one_c_panel(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)

        card = Card()
        form_layout = QHBoxLayout()
        form = QFormLayoutLike()
        self.mode = QComboBox()
        self.mode.addItems(["http_service", "odata"])
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("http://192.168.1.100/trade_base/hs/ai_sales/")
        self.odata_path = QLineEdit()
        self.odata_path.setPlaceholderText("/odata/standard.odata")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.timeout = IntEdit(1500, 300, 5000, suffix=" мс")
        self.retries = IntEdit(1, 0, 4)
        form.add_row("Режим подключения", self.mode)
        form.add_row("URL базы / HTTP-сервиса", self.base_url)
        form.add_row("Путь OData", self.odata_path)
        form.add_row("Технический пользователь", self.username)
        form.add_row("Пароль", self.password)
        form.add_row("Таймаут запроса", self.timeout)
        form.add_row("Повторов при сбое", self.retries)
        card.layout().addLayout(form)

        probe_row = QHBoxLayout()
        self.test_btn = QPushButton("  Проверить связь с 1С")
        self.test_btn.setIcon(icons.icon("zap"))
        self.test_btn.clicked.connect(self._run_probe)
        self.probe_badge = Badge("Не проверено")
        probe_row.addWidget(self.test_btn)
        probe_row.addWidget(self.probe_badge)
        probe_row.addStretch(1)
        card.layout().addLayout(probe_row)
        box.addWidget(card)

        mapping_card = Card()
        mapping_card.add(SectionLabel("Маппинг полей OData"))
        self.mapping_table = QTableWidget(len(_MAPPING_ROWS), 2)
        self.mapping_table.setHorizontalHeaderLabels(["Поле агента", "Имя реквизита 1С"])
        self.mapping_table.verticalHeader().setVisible(False)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mapping_table.setColumnWidth(0, 180)
        for row, (key, label) in enumerate(_MAPPING_ROWS):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.mapping_table.setItem(row, 0, item)
            self.mapping_table.setRowHeight(row, 30)
        mapping_card.add(self.mapping_table)
        box.addWidget(mapping_card, 1)
        return panel

    def _run_probe(self) -> None:
        self._collect_one_c()
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
        except Exception as exc:  # noqa: BLE001
            self.probe_badge.setText(f"Нет связи: {str(exc)[:48]}")
            self.probe_badge.set_tone("danger")
            return
        warehouses = result.get("warehouses") or []
        self.probe_badge.setText(
            f"Связь OK · складов: {len(warehouses)}" if warehouses else "Связь OK")
        self.probe_badge.set_tone("ok")

    # ---------------------------------------------------------------- state
    def _load_state(self) -> None:
        c = self.settings.one_c
        self.mode.setCurrentText(c.mode)
        self.base_url.setText(c.base_url)
        self.odata_path.setText(c.odata_path)
        self.username.setText(c.username)
        self.password.setText(c.password)
        self.timeout.setValue(c.timeout_ms)
        self.retries.setValue(c.retries)
        for row, (key, _label) in enumerate(_MAPPING_ROWS):
            self.mapping_table.setItem(row, 1, QTableWidgetItem(c.mapping.get(key, "")))

        is_local = self.settings.catalog.mode == "local"
        self.btn_local.setChecked(is_local)
        self.btn_one_c.setChecked(not is_local)
        self.stack.setCurrentIndex(0 if is_local else 1)
        self._reload_preview()

    def _collect_one_c(self) -> None:
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
        if self.stack.currentIndex() == 0:
            self.settings.catalog.mode = "local"
        else:
            self._collect_one_c()
            self.settings.catalog.mode = (
                "one_c_odata" if self.settings.one_c.mode == "odata" else "one_c_http"
            )
        self.settings.save()
        self.saved.emit()


# QFormLayout kept breaking under the QSS style; this tiny helper gives the
# same two-column rhythm with explicit label widgets.
class QFormLayoutLike(QVBoxLayout):
    def __init__(self):
        super().__init__()
        self.setSpacing(10)

    def add_row(self, label: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        caption = QLabel(label)
        caption.setProperty("role", "fieldLabel")
        caption.setFixedWidth(210)
        row.addWidget(caption)
        row.addWidget(widget, 1)
        self.addLayout(row)
