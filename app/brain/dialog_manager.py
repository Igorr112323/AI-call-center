"""Dialog brain: explicit state machine + slot filling + LLM tool-calling loop."""
from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field

from ..config import ScenarioConfig
from ..core.models import Order, OrderItem
from ..integrations.catalog_provider import extract_params
from .llm import LlmClient, LlmError, TurnCancelled
from .prompts import GREETING, build_system_prompt
from .tools import TOOL_SCHEMAS, ToolExecutor

_CONFIRM_WORDS = ("да", "оформл", "заказыва", "записыва", "беру", "согласен", "подтвержда")
_CATEGORY_WORDS = ("актуатор", "привод", "сервопривод", "выключатель", "двигатель", "клапан")

log = logging.getLogger(__name__)


class DialogState(str, enum.Enum):
    GREETING = "greeting"
    COLLECTING = "collecting"
    QUOTING = "quoting"
    HANDLING_OBJECTION = "objection"
    CONFIRMING = "confirming"
    ORDER_CREATED = "order_created"
    CLOSED = "closed"


# Allowed transitions make illegal jumps impossible (and unit-testable).
_TRANSITIONS: dict[DialogState, set[DialogState]] = {
    DialogState.GREETING: {DialogState.COLLECTING, DialogState.CLOSED},
    DialogState.COLLECTING: {
        DialogState.COLLECTING, DialogState.QUOTING, DialogState.HANDLING_OBJECTION,
        DialogState.CONFIRMING, DialogState.CLOSED,
    },
    DialogState.QUOTING: {
        DialogState.HANDLING_OBJECTION, DialogState.CONFIRMING,
        DialogState.ORDER_CREATED, DialogState.COLLECTING, DialogState.CLOSED,
    },
    DialogState.HANDLING_OBJECTION: {
        DialogState.QUOTING, DialogState.CONFIRMING, DialogState.ORDER_CREATED,
        DialogState.COLLECTING, DialogState.CLOSED,
    },
    DialogState.CONFIRMING: {
        DialogState.ORDER_CREATED, DialogState.HANDLING_OBJECTION, DialogState.CLOSED,
    },
    DialogState.ORDER_CREATED: {DialogState.CLOSED},
    DialogState.CLOSED: set(),
}

_OBJECTION_TRIGGERS = ("дорого", "скидк", "нет в наличии", "подума", "конкурент", "дешевле")


@dataclass
class TurnResult:
    reply_text: str
    state: DialogState
    tool_names: list[str] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    order: Order | None = None
    cancelled: bool = False


class DialogManager:
    """One instance per live call."""

    def __init__(self, llm: LlmClient, scenario: ScenarioConfig,
                 executor: ToolExecutor):
        self.llm = llm
        self.scenario = scenario
        self.executor = executor
        self.state = DialogState.GREETING
        self.messages: list[dict] = [{"role": "system", "content": build_system_prompt(scenario)}]
        self._required = [s.name for s in scenario.slots if s.required]
        self.order: Order | None = None

    def open_greeting(self) -> str:
        self._transition(DialogState.COLLECTING)
        return GREETING

    @property
    def slots(self) -> dict:
        return self.executor.slots

    def missing_required_slots(self) -> list[str]:
        return [name for name in self._required if not self.executor.slots.get(name)]

    async def handle_utterance(self, text: str,
                               cancel: asyncio.Event | None = None) -> TurnResult:
        # Fully offline path: a real LlmClient without an API key falls back to
        # the deterministic selling brain; test doubles keep their scripted path.
        if isinstance(self.llm, LlmClient) and not self.llm.cfg.api_key:
            return await self._offline_turn(text)

        cancel = cancel or asyncio.Event()
        self.messages.append({"role": "user", "content": text})
        tool_names: list[str] = []

        try:
            # Bounded loop: each turn may do several tool calls, never unbounded.
            for _ in range(6):
                if cancel is not None and cancel.is_set():
                    raise TurnCancelled()
                try:
                    assistant = await self.llm.complete(self.messages, TOOL_SCHEMAS, cancel)
                except LlmError as exc:
                    log.error("LLM unavailable, deterministic fallback: %s", exc)
                    reply = self._fallback_reply()
                    break
                self.messages.append(assistant.as_chat_message())

                if not assistant.tool_calls:
                    reply = assistant.content or "Простите, повторите, пожалуйста."
                    break

                for call in assistant.tool_calls:
                    tool_names.append(call.name)
                    result = await self.executor.execute(call.name, call.arguments)
                    self.messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
            else:
                reply = "Давайте я зафиксирую это и вернусь к вам с ответом."
        except TurnCancelled:
            log.info("turn cancelled by barge-in")
            return TurnResult("", self.state, tool_names, dict(self.slots),
                              self.missing_required_slots(), self.order, cancelled=True)

        self._advance_state(text, tool_names)
        if self.executor.order_result and self.order is None:
            self.order = self._build_order(self.executor.order_result)
        return TurnResult(reply, self.state, tool_names, dict(self.slots),
                          self.missing_required_slots(), self.order)

    # ------------------------------------------------------------- offline brain
    async def _offline_turn(self, text: str) -> TurnResult:
        """Deterministic slot-filling sales flow without any external LLM API."""
        self.messages.append({"role": "user", "content": text})
        tool_names: list[str] = []
        lowered = text.lower()

        for rule in self.scenario.objections:
            if rule["trigger"].lower() in lowered:
                self._advance_state(text, [])
                self.messages.append({"role": "assistant", "content": rule["response"]})
                return self._result(rule["response"], tool_names)

        params = extract_params(text)
        category = next((w for w in _CATEGORY_WORDS if w in lowered), None)
        if category:
            params["category"] = category
        if params:
            await self.executor.execute("collect_slots", params)
            tool_names.append("collect_slots")

        if self.executor.last_product and any(w in lowered for w in _CONFIRM_WORDS):
            result = await self.executor.execute("create_order", {})
            tool_names.append("create_order")
            reply = self._offline_order_reply(result)
        elif self._ready_for_quote(params):
            result = await self.executor.execute("check_catalog_and_price", {
                "category": self.executor.slots.get("category", ""),
                "voltage": self.executor.slots.get("voltage"),
                "stroke_mm": self.executor.slots.get("stroke_mm"),
                "force_n": self.executor.slots.get("force_n"),
            })
            tool_names.append("check_catalog_and_price")
            reply = self._offline_quote_reply()
        else:
            reply = self._next_slot_question()

        self._advance_state(text, tool_names)
        if self.executor.order_result and self.order is None:
            self.order = self._build_order(self.executor.order_result)
        self.messages.append({"role": "assistant", "content": reply})
        return self._result(reply, tool_names)

    def _ready_for_quote(self, params: dict) -> bool:
        slots = self.executor.slots
        return bool(slots.get("voltage") and slots.get("stroke_mm")) \
            and self.executor.last_product is None

    def _next_slot_question(self) -> str:
        for slot in self.scenario.slots:
            if slot.required and not self.executor.slots.get(slot.name):
                return slot.question
        return "Продиктуйте, пожалуйста, параметры оборудования."

    def _offline_quote_reply(self) -> str:
        product = self.executor.last_product
        if not product or not product.found:
            return ("К сожалению, по таким параметрам позиция не найдена в прайсе. "
                    "Можем подобрать аналог — продиктуйте другие характеристики.")
        qty = self.executor.slots.get("quantity") or 1
        return (
            f"Подобрали: {product.name}, артикул {product.article}. "
            f"Цена {_format_money(product.price)} рублей, в наличии {int(product.stock)} штук. "
            f"За {qty} шт. получится {_format_money(product.price * qty)} рублей. Оформляем заказ?"
        )

    def _offline_order_reply(self, result_json: str) -> str:
        import json
        result = json.loads(result_json)
        if not result.get("ok"):
            return "Не получилось зафиксировать заказ, повторите, пожалуйста, ещё раз."
        number = result.get("number_1c") or result.get("number") or "—"
        return f"Готово, заказ {number} зафиксирован. Менеджер свяжется с вами для подтверждения."

    def _result(self, reply: str, tool_names: list[str]):
        return TurnResult(reply, self.state, tool_names, dict(self.executor.slots),
                          self.missing_required_slots(), self.order)

    # ------------------------------------------------------------------ internals
    def _fallback_reply(self) -> str:
        """Deterministic degradation when the LLM endpoint is unreachable."""
        for rule in self.scenario.objections:
            if rule["trigger"].lower() in self.messages[-1].get("content", "").lower():
                return rule["response"]
        for slot in self.scenario.slots:
            if slot.required and not self.executor.slots.get(slot.name):
                return slot.question
        if self.executor.last_product:
            return "Давайте зафиксируем заказ — согласовываем?"
        return "Простите, связь с моделью нестабильна. Продублируйте, пожалуйста, ещё раз."

    def _advance_state(self, user_text: str, tool_names: list[str]) -> None:
        if "create_order" in tool_names and self.executor.order_result:
            target = DialogState.ORDER_CREATED
        elif any(t in user_text.lower() for t in _OBJECTION_TRIGGERS):
            target = DialogState.HANDLING_OBJECTION
        elif "check_catalog_and_price" in tool_names:
            target = DialogState.QUOTING
        elif self.missing_required_slots():
            target = DialogState.COLLECTING
        elif self.executor.last_product:
            target = DialogState.CONFIRMING
        else:
            target = self.state

        if target is DialogState.HANDLING_OBJECTION:
            self._transition(target)
            # objections are a transient state; next observation re-enters the flow
            self._transition(
                DialogState.QUOTING if self.executor.last_product else DialogState.COLLECTING
            )
            return
        self._transition(target)

    def _transition(self, target: DialogState) -> None:
        if target == self.state:
            return
        if target not in _TRANSITIONS[self.state]:
            log.warning("illegal transition %s -> %s, keeping state", self.state, target)
            return
        log.info("dialog state: %s -> %s", self.state.value, target.value)
        self.state = target

    def _build_order(self, result: dict) -> Order:
        items = [
            OrderItem(i["article"], i["name"], float(i["quantity"]), float(i["price"]))
            for i in result.get("items", [])
        ]
        return Order(
            call_id=None,
            number_1c=result.get("number_1c") or None,
            posted=bool(result.get("ok")),
            comment=self.slots.get("delivery_city", ""),
            items=items,
        )

    def close(self) -> None:
        self._transition(DialogState.CLOSED)


def _format_money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")
