"""Tab 2 — persona prompt, slot-filling table, objection playbook."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import AppSettings, SlotSpec
from . import icons
from .widgets import Card, IconButton, SectionLabel


class ScenarioTab(QWidget):
    saved = Signal()

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Сценарий и промпт")
        title.setProperty("role", "screenTitle")
        header.addWidget(title)
        header.addStretch(1)
        save = QPushButton("  Сохранить сценарий")
        save.setProperty("variant", "primary")
        save.setIcon(icons.icon("check", "#062018"))
        save.clicked.connect(self._save)
        header.addWidget(save)
        root.addLayout(header)

        persona_card = Card()
        persona_card.add(SectionLabel("Личность и роль продавца"))
        self.persona = QPlainTextEdit()
        self.persona.setMinimumHeight(110)
        persona_card.add(self.persona)
        root.addWidget(persona_card)

        slots_card = Card()
        self.slots_table = QTableWidget(0, 4)
        self.slots_table.setHorizontalHeaderLabels(["Параметр", "Обязателен", "Тип", "Вопрос клиенту"])
        self.slots_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.slots_table.setColumnWidth(0, 150)
        self.slots_table.setColumnWidth(1, 118)
        self.slots_table.setColumnWidth(2, 110)
        self.slots_table.verticalHeader().setVisible(False)
        self.slots_table.setMinimumHeight(228)
        slots_head = QHBoxLayout()
        slots_head.addWidget(SectionLabel("Обязательные вопросы (слоты)"))
        slots_head.addStretch(1)
        add_slot = IconButton("plus", "Добавить слот")
        del_slot = IconButton("trash", "Удалить выбранный")
        add_slot.clicked.connect(lambda: self._add_slot_row())
        del_slot.clicked.connect(lambda: self._remove_selected(self.slots_table))
        slots_head.addWidget(add_slot)
        slots_head.addWidget(del_slot)
        slots_card.layout().addLayout(slots_head)
        slots_card.add(self.slots_table)
        root.addWidget(slots_card)

        obj_card = Card()
        self.obj_table = QTableWidget(0, 2)
        self.obj_table.setHorizontalHeaderLabels(["Триггер клиента", "Ответ продавца"])
        self.obj_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.obj_table.setColumnWidth(0, 180)
        self.obj_table.verticalHeader().setVisible(False)
        self.obj_table.setMinimumHeight(178)
        obj_head = QHBoxLayout()
        obj_head.addWidget(SectionLabel("Работа с возражениями"))
        obj_head.addStretch(1)
        add_obj = IconButton("plus", "Добавить правило")
        del_obj = IconButton("trash", "Удалить выбранное")
        add_obj.clicked.connect(lambda: self._add_objection_row())
        del_obj.clicked.connect(lambda: self._remove_selected(self.obj_table))
        obj_head.addWidget(add_obj)
        obj_head.addWidget(del_obj)
        obj_card.layout().addLayout(obj_head)
        obj_card.add(self.obj_table)
        root.addWidget(obj_card, 1)

        self.load_from(settings)

    # ------------------------------------------------------------------ data
    def load_from(self, settings: AppSettings) -> None:
        self.persona.setPlainText(settings.scenario.persona)
        self.slots_table.setRowCount(0)
        for slot in settings.scenario.slots:
            self._add_slot_row(slot)
        self.obj_table.setRowCount(0)
        for rule in settings.scenario.objections:
            self._add_objection_row(rule["trigger"], rule["response"])

    def _add_slot_row(self, slot: SlotSpec | None = None) -> None:
        row = self.slots_table.rowCount()
        self.slots_table.insertRow(row)
        self.slots_table.setRowHeight(row, 30)
        name = QTableWidgetItem(slot.name if slot else "")
        check = QCheckBox()
        check.setChecked(slot.required if slot else True)
        check_container = QWidget()
        check_layout = QHBoxLayout(check_container)
        check_layout.setContentsMargins(10, 0, 0, 0)
        check_layout.addWidget(check)
        type_combo = QComboBox()
        type_combo.addItems(["string", "integer", "float"])
        if slot:
            type_combo.setCurrentText(slot.data_type)
        question = QTableWidgetItem(slot.question if slot else "")
        self.slots_table.setItem(row, 0, name)
        self.slots_table.setCellWidget(row, 1, check_container)
        self.slots_table.setCellWidget(row, 2, type_combo)
        self.slots_table.setItem(row, 3, question)

    def _add_objection_row(self, trigger: str = "", response: str = "") -> None:
        row = self.obj_table.rowCount()
        self.obj_table.insertRow(row)
        self.obj_table.setRowHeight(row, 30)
        self.obj_table.setItem(row, 0, QTableWidgetItem(trigger))
        self.obj_table.setItem(row, 1, QTableWidgetItem(response))

    def _remove_selected(self, table: QTableWidget) -> None:
        rows = {idx.row() for idx in table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            table.removeRow(row)

    def _save(self) -> None:
        scenario = self.settings.scenario
        scenario.persona = self.persona.toPlainText().strip()
        slots: list[SlotSpec] = []
        for row in range(self.slots_table.rowCount()):
            name_item = self.slots_table.item(row, 0)
            question_item = self.slots_table.item(row, 3)
            name = name_item.text().strip() if name_item else ""
            if not name:
                continue
            check = self.slots_table.cellWidget(row, 1).findChild(QCheckBox)
            type_combo = self.slots_table.cellWidget(row, 2)
            slots.append(SlotSpec(
                name=name,
                required=check.isChecked(),
                data_type=type_combo.currentText(),
                question=question_item.text().strip() if question_item else "",
            ))
        scenario.slots = slots
        objections = []
        for row in range(self.obj_table.rowCount()):
            trigger = self.obj_table.item(row, 0)
            response = self.obj_table.item(row, 1)
            if trigger and trigger.text().strip():
                objections.append({
                    "trigger": trigger.text().strip(),
                    "response": response.text().strip() if response else "",
                })
        scenario.objections = objections
        self.settings.save()
        self.saved.emit()
