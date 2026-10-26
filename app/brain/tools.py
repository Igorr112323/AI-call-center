"""Function-calling schemas and the tool executor bound to 1C."""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from ..core.models import OrderItem
from ..integrations.catalog_provider import CatalogError
from ..integrations.one_c_client import OneCError

log = logging.getLogger(__name__)

CATALOG_ERRORS = (OneCError, CatalogError)

ToolCallback = Callable[[str], Awaitable[None]]

COLLECT_SLOTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "collect_slots",
        "description": "Фиксирует параметры заказа, распознанные в речи клиента. Передавай только реально названные значения.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Категория товара, например 'актуатор'"},
                "voltage": {"type": "integer", "description": "Напряжение питания, В: 12, 24 или 220"},
                "stroke_mm": {"type": "integer", "description": "Ход штока в миллиметрах"},
                "force_n": {"type": "integer", "description": "Усилие в Ньютонах"},
                "quantity": {"type": "integer", "description": "Количество единиц"},
                "delivery_city": {"type": "string", "description": "Город доставки"},
            },
        },
    },
}

CHECK_CATALOG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_catalog_and_price",
        "description": "Запрашивает в 1С актуальную цену и наличие товара по параметрам. "
                       "Вызывать вместо любых предположений о цене.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Категория товара, например 'актуатор'"},
                "voltage": {"type": "integer", "description": "Напряжение питания (12, 24, 220)"},
                "stroke_mm": {"type": "integer", "description": "Ход штока в миллиметрах"},
                "force_n": {"type": "integer", "description": "Усилие в Ньютонах или кг"},
            },
            "required": ["voltage", "stroke_mm"],
        },
    },
}

CREATE_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_order",
        "description": "Создаёт в 1С документ заказа по согласованным позициям. Вызывается после явного согласия клиента.",
        "parameters": {
            "type": "object",
            "properties": {
                "comment": {"type": "string", "description": "Комментарий к заказу"},
                "delivery_address": {"type": "string", "description": "Адрес доставки, если назван"},
            },
        },
    },
}

TOOL_SCHEMAS = [COLLECT_SLOTS_SCHEMA, CHECK_CATALOG_SCHEMA, CREATE_ORDER_SCHEMA]


class ToolExecutor:
    """Stateful executor: keeps slots and the last quoted product for the session.

    ``catalog`` is any BaseCatalogProvider — local SQLite price list or live 1C.
    """

    def __init__(self, catalog, phone: str,
                 on_tool_start: ToolCallback | None = None):
        self.catalog = catalog
        # backwards-compatible alias for callers/tests written against 1C
        self.one_c = catalog
        self.phone = phone
        self.on_tool_start = on_tool_start
        self.slots: dict = {}
        self.last_product = None  # ProductStock
        self.order_result: dict | None = None

    async def execute(self, name: str, arguments: dict) -> str:
        if self.on_tool_start is not None:
            try:
                await self.on_tool_start(name)
            except Exception:  # filler must never break the turn
                log.exception("filler callback failed")

        if name == "collect_slots":
            for key, value in arguments.items():
                if value is not None and value != "":
                    self.slots[key] = value
            return json.dumps({"ok": True, "slots": self.slots}, ensure_ascii=False)

        if name == "check_catalog_and_price":
            for key in ("category", "voltage", "stroke_mm", "force_n"):
                if arguments.get(key) is not None:
                    self.slots.setdefault(key, arguments[key])
            query = arguments.get("category") or self.slots.get("category") or ""
            try:
                product = await self.catalog.get_product_price_and_stock(query, arguments)
            except CATALOG_ERRORS as exc:
                return json.dumps(
                    {"ok": False, "error": "Каталог недоступен, извинись и предложи перезвонить",
                     "detail": str(exc)}, ensure_ascii=False)
            self.last_product = product
            return json.dumps({
                "ok": product.found,
                "article": product.article,
                "name": product.name,
                "price": product.price,
                "stock": product.stock,
                "warehouse": product.warehouse,
            }, ensure_ascii=False)

        if name == "create_order":
            if self.last_product is None or not self.last_product.found:
                return json.dumps({"ok": False, "error": "нет согласованной номенклатуры"},
                                  ensure_ascii=False)
            qty = float(self.slots.get("quantity", 1) or 1)
            item = OrderItem(
                article=self.last_product.article,
                name=self.last_product.name,
                quantity=qty,
                price=self.last_product.price,
            )
            try:
                result = await self.catalog.create_lead_or_order(
                    self.phone, [item],
                    comment=arguments.get("comment", ""),
                    address=arguments.get("delivery_address", ""),
                )
            except CATALOG_ERRORS as exc:
                return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            self.order_result = result
            result["items"] = [{"article": item.article, "name": item.name,
                                "quantity": item.quantity, "price": item.price, "sum": item.sum}]
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"unknown tool {name}"}, ensure_ascii=False)
