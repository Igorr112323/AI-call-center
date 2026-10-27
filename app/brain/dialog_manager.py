"""Dialog brain: explicit state machine + slot filling + LLM tool-calling loop."""
from __future__ import annotations

import asyncio
import enum
import logging
from collections import deque
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
    DialogState.ORDER_CREATED: {
        DialogState.CLOSED, DialogState.COLLECTING, DialogState.QUOTING,
        DialogState.HANDLING_OBJECTION, DialogState.CONFIRMING,
    },
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
    silence_timeout: bool = False


# Second-line answers when the same objection comes up again — the offline
# brain must never parrot the identical phrase like a broken record.
_OBJECTION_FOLLOWUPS: dict[str, str] = {
    "дорого": (
        "Понимаю обеспокоенность. Это заводская цена с гарантией и наличием на "
        "складе: дешёвые аналоги часто выходят дороже из-за простоя и замены."
    ),
    "скидку": (
        "Давайте зафиксирую запрос: при заказе от десяти единиц согласуем "
        "индивидуальную скидку и сроки поставки прямо сейчас."
    ),
    "нет в наличии": (
        "Смотрю ближайшее поступление и аналоги — подберём позицию без потери "
        "по характеристикам, чтобы не останавливать вашу линию."
    ),
    "подумаю": (
        "Конечно. Зафиксирую цену и наличие до конца дня — если передумаете, "
        "позвоните, параметры уже будут у меня под рукой."
    ),
    "робот": (
        "Я голосовой ассистент, но работаю по реальному прайсу и складу; если "
        "нужен живой менеджер — оставьте вопрос, и он перезвонит в течение часа."
    ),
    "человек": (
        "Записываю запрос на живого менеджера. Подскажите удобное время звонка "
        "и суть вопроса, чтобы он подготовил цену и наличие заранее."
    ),
}

SILENCE_PROMPTS = (
    "Алло, вас не слышно, вы на связи?",
    "Переспросите, пожалуйста, я фиксирую параметры оборудования.",
    "Связь прервалась — перезвоним, как только она восстановится. До свидания.",
)

# Several natural phrasings per slot: the offline brain rotates them so the
# same missing parameter never triggers the identical robotic question twice.
_SLOT_QUESTION_VARIANTS: dict[str, tuple[str, ...]] = {
    "category": (
        "Какое оборудование вас интересует?",
        "Подскажите, что подбираем — актуаторы, рукава высокого давления или коробки?",
        "По какому типу оборудования делаем подбор?",
    ),
    "voltage": (
        "Какое напряжение питания нужно?",
        "Уточните напряжение, например 12, 24 или 220 вольт.",
        "На сколько вольт подбираем оборудование?",
    ),
    "stroke_mm": (
        "Какой ход штока, в миллиметрах?",
        "Уточните длину хода штока в миллиметрах.",
        "Сколько миллиметров рабочего хода нужно?",
    ),
    "force_n": (
        "Какое рабочее усилие требуется?",
        "Уточните необходимое усилие в ньютонах.",
    ),
    "quantity": (
        "Сколько единиц планируете заказывать?",
        "Какое количество позиций нужно?",
        "Сколько штук закладываем в заказ?",
    ),
    "delivery_city": (
        "В какой город нужна доставка?",
        "Куда организовать поставку?",
    ),
}

_SLOT_LABELS = {
    "category": "тип оборудования",
    "voltage": "напряжение",
    "stroke_mm": "ход штока",
    "force_n": "усилие",
    "quantity": "количество",
    "delivery_city": "город доставки",
}

_GREETING_WORDS = (
    "привет", "здравствуй", "добрый день", "доброе утро", "добрый вечер",
    "здарова", "хай", "доброго дня",
)
_THANKS_WORDS = ("спасибо", "благодарю", "спс", "благодарность")
_CAPABILITY_WORDS = (
    "что ты умеешь", "чем можешь помочь", "твои возможности",
    "что умеешь", "умеешь", "помоги с", "чем поможешь", "твои функции",
)
_REPEAT_WORDS = ("повтори", "не расслышал", "не понял", "что-что", "ещё раз")

_CAPABILITY_REPLY = (
    "Я подбираю оборудование по вашим параметрам, проверяю цену и наличие по "
    "складу, считаю сумму заказа и сразу фиксирую заявку."
)
_HUMAN_HANDOFF = (
    "Если удобнее, оставьте номер и удобное время — передам живому менеджеру, "
    "он перезвонит и сам всё зафиксирует."
)
# System nudge injected when a cloud LLM parrots its previous reply verbatim.
_ANTI_LOOP_NUDGE = (
    "Ты дословно повторил предыдущий ответ. Не повторяйся: задай один конкретный "
    "вопрос по следующему недостающему параметру или предложи следующий шаг."
)


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
        self.silence_count = 0
        self._objection_hits: dict[str, int] = {}
        # Anti-loop machinery for the deterministic brain
        self._slot_ask_counts: dict[str, int] = {}
        self._last_question_slot: str | None = None
        self._unrecognized = 0
        self._recent_replies: deque[str] = deque(maxlen=5)
        self._recent_user_texts: deque[str] = deque(maxlen=3)

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
        self.silence_count = 0  # any real phrase resets the silence watchdog
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
                    content = assistant.content or "Простите, повторите, пожалуйста."
                    # Anti-loop: if the cloud model parrots its previous reply,
                    # nudge it once to ask the next concrete question instead.
                    previous = next(
                        (m.get("content") for m in reversed(self.messages[:-1])
                         if m.get("role") == "assistant" and m.get("content")),
                        None,
                    )
                    if content == previous:
                        self.messages.append(
                            {"role": "system", "content": _ANTI_LOOP_NUDGE})
                        continue
                    reply = content
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

    async def handle_silence(self) -> TurnResult:
        """Watchdog turn for a non-responding customer.

        First two missed turns ask for confirmation; the third closes the
        call automatically. Real utterances reset the counter (handle_utterance).
        """
        if self.state is DialogState.CLOSED:
            return self._result("", [])
        self.silence_count += 1
        timed_out = self.silence_count >= 3
        reply = SILENCE_PROMPTS[min(self.silence_count - 1, len(SILENCE_PROMPTS) - 1)]
        if timed_out:
            self.close()
        self.messages.append({"role": "assistant", "content": reply})
        return TurnResult(reply, self.state, [], dict(self.executor.slots),
                          self.missing_required_slots(), self.order,
                          silence_timeout=timed_out)

    # ------------------------------------------------------------- offline brain
    async def _offline_turn(self, text: str) -> TurnResult:
        """Deterministic slot-filling sales flow without any external LLM API."""
        self.messages.append({"role": "user", "content": text})
        tool_names: list[str] = []
        lowered = text.lower()

        for rule in self.scenario.objections:
            trigger = rule["trigger"].lower()
            if trigger in lowered:
                hits = self._objection_hits.get(trigger, 0)
                self._objection_hits[trigger] = hits + 1
                reply = rule["response"] if hits == 0 else _OBJECTION_FOLLOWUPS.get(
                    trigger, rule["response"])
                self._unrecognized = 0
                reply = self._unique_reply(reply)
                self._advance_state(text, [])
                self.messages.append({"role": "assistant", "content": reply})
                return self._result(reply, tool_names)

        ack = self._smalltalk_ack(lowered)
        params = extract_params(text)
        category = next((w for w in _CATEGORY_WORDS if w in lowered), None)
        if category:
            params["category"] = category
        progress = bool(params)
        if progress:
            self._unrecognized = 0
            await self.executor.execute("collect_slots", params)
            tool_names.append("collect_slots")
        else:
            self._unrecognized += 1

        # Slot change invalidated the provisional quote/order — drop the stale
        # local order object so the flow re-quotes instead of double-booking.
        if self.executor.last_product is None and self.order is not None:
            self.order = None

        normalized_text = text.strip().lower()
        repeated_input = normalized_text in self._recent_user_texts
        self._recent_user_texts.append(normalized_text)

        if self.executor.last_product and any(w in lowered for w in _CONFIRM_WORDS):
            if self.state is DialogState.ORDER_CREATED:
                reply = (
                    f"Заказ {self.order.number_1c if self.order else ''} уже "
                    "зафиксирован. Если нужно изменить параметры — назовите их, пересчитаю."
                ).replace("  ", " ").strip()
                reply = self._unique_reply(reply)
                self.messages.append({"role": "assistant", "content": reply})
                return self._result(reply, tool_names)
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
            self._unrecognized = 0
            reply = self._offline_quote_reply()
        elif not progress and (self._unrecognized >= 2 or repeated_input):
            # Off-script or repeated misunderstood phrase: steer with a summary
            reply = self._confused_reply()
        else:
            question = self._next_slot_question()
            reply = f"{ack} {question}".strip() if ack else question

        reply = self._unique_reply(reply)
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
                variants = _SLOT_QUESTION_VARIANTS.get(slot.name, (slot.question,))
                n = self._slot_ask_counts.get(slot.name, 0)
                self._slot_ask_counts[slot.name] = n + 1
                self._last_question_slot = slot.name
                return variants[n % len(variants)]
        return "Продиктуйте, пожалуйста, параметры оборудования."

    def _collected_summary(self) -> str:
        """Human-readable list of slots already captured in this call."""
        parts = []
        for name, value in self.executor.slots.items():
            if value in (None, "", 0):
                continue
            label = _SLOT_LABELS.get(name, name)
            parts.append(f"{label}: {value}")
        return ", ".join(parts)

    def _smalltalk_ack(self, lowered: str) -> str:
        """Recognise greetings/thanks/capability/repeat requests off-script."""
        if any(w in lowered for w in _GREETING_WORDS):
            return "Здравствуйте!"
        if any(w in lowered for w in _THANKS_WORDS):
            return "Пожалуйста!"
        if any(w in lowered for w in _REPEAT_WORDS):
            return "Конечно, повторяю."
        if any(w in lowered for w in _CAPABILITY_WORDS):
            return _CAPABILITY_REPLY
        return ""

    def _confused_reply(self) -> str:
        """Steering answer when the customer says nothing the parser understands."""
        pieces = []
        if collected := self._collected_summary():
            pieces.append(f"Пока зафиксировал: {collected}.")
        if missing := [_SLOT_LABELS.get(s, s) for s in self.missing_required_slots()]:
            pieces.append("Не хватает: " + ", ".join(missing) + ".")
        pieces.append(self._next_slot_question())
        if self._unrecognized >= 3:
            pieces.append(_HUMAN_HANDOFF)
        return " ".join(pieces)

    def _unique_reply(self, reply: str) -> str:
        """Never emit the same sentence twice; rotate variants or paraphrase."""
        if reply not in self._recent_replies:
            self._recent_replies.append(reply)
            return reply
        if self._last_question_slot:
            variants = _SLOT_QUESTION_VARIANTS.get(self._last_question_slot, ())
            for alt in variants:
                if alt not in self._recent_replies:
                    self._recent_replies.append(alt)
                    return alt
        wrapped = "Уточню ещё раз: " + reply[0].lower() + reply[1:]
        self._recent_replies.append(wrapped)
        return wrapped

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
                return self._unique_reply(rule["response"])
        if self.missing_required_slots():
            return self._unique_reply(self._next_slot_question())
        if self.executor.last_product:
            return self._unique_reply(
                "Давайте зафиксируем заказ — согласовываем?")
        return self._unique_reply(
            "Простите, связь с моделью нестабильна. Продублируйте, пожалуйста, ещё раз.")

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
