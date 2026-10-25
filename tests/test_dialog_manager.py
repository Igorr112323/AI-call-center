import httpx
import pytest

from app.brain.dialog_manager import DialogManager, DialogState
from app.brain.llm import AssistantMessage, LlmError, ToolCall
from app.config import AppSettings, OneCConfig
from app.integrations.one_c_client import OneCClient
from app.brain.tools import ToolExecutor


class ScriptedLlm:
    """Returns queued messages; records the conversation for assertions."""

    def __init__(self, script: list):
        self.script = list(script)
        self.messages_seen: list[list[dict]] = []

    async def complete(self, messages, tools=None, cancel=None):
        self.messages_seen.append(list(messages))
        if not self.script:
            return AssistantMessage(content="Что-нибудь ещё?")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _one_c() -> OneCClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("catalog/lookup"):
            return httpx.Response(200, json={
                "found": True, "article": "ACT-24-300",
                "name": "Актуатор 24В 300мм", "price": 11500,
                "stock": 14, "warehouse": "Основной склад"})
        return httpx.Response(200, json={"ok": True, "number": "Заказ 8412"})

    return OneCClient(OneCConfig(base_url="http://1c.local/hs/ai_sales/"),
                      transport=httpx.MockTransport(handler))


async def test_full_sales_flow_slots_quote_order(settings: AppSettings):
    llm = ScriptedLlm([
        AssistantMessage(content="Какое напряжение?", tool_calls=[
            ToolCall("t1", "collect_slots", {"category": "актуатор", "voltage": 24})]),
        AssistantMessage(content="Какой ход штока?", tool_calls=[
            ToolCall("t2", "collect_slots", {"stroke_mm": 300, "quantity": 1})]),
        AssistantMessage(tool_calls=[
            ToolCall("t3", "check_catalog_and_price",
                     {"category": "актуатор", "voltage": 24, "stroke_mm": 300})]),
        AssistantMessage(content="Цена 11 500 рублей, в наличии 14 штук. Оформляем?"),
        AssistantMessage(tool_calls=[ToolCall("t4", "create_order", {})]),
        AssistantMessage(content="Заказ зафиксирован, номер 8412."),
    ])
    executor = ToolExecutor(_one_c(), "+79182041122")
    dm = DialogManager(llm, settings.scenario, executor)

    assert dm.open_greeting()
    # One utterance drains the whole tool-call chain until a plain reply
    r1 = await dm.handle_utterance("Нужен актуатор на 24 вольта, ход 300, одна штука")
    assert dm.slots["voltage"] == 24 and dm.slots["stroke_mm"] == 300
    assert r1.state is DialogState.QUOTING
    assert executor.last_product.price == 11500
    assert "11 500" in r1.reply_text

    r3 = await dm.handle_utterance("Да, оформляем")
    assert r3.state is DialogState.ORDER_CREATED
    assert r3.order is not None and r3.order.total == 11500
    assert dm.executor.order_result["number_1c"] == "Заказ 8412"


async def test_illegal_transitions_are_blocked(settings: AppSettings):
    llm = ScriptedLlm([])
    dm = DialogManager(llm, settings.scenario, ToolExecutor(_one_c(), "+7000000000"))
    dm.open_greeting()
    # ORDER_CREATED is unreachable directly from COLLECTING
    dm._transition(DialogState.ORDER_CREATED)
    assert dm.state is DialogState.COLLECTING


async def test_llm_outage_uses_deterministic_fallback(settings: AppSettings):
    llm = ScriptedLlm([LlmError("503 unavailable")])
    dm = DialogManager(llm, settings.scenario, ToolExecutor(_one_c(), "+7000000000"))
    dm.open_greeting()
    result = await dm.handle_utterance("Здравствуйте")
    assert result.reply_text  # a slot question from the scenario playbook
    assert "?" in result.reply_text or result.reply_text.endswith("?")


async def test_barge_in_cancels_turn(settings: AppSettings):
    import asyncio

    class CancellingLlm:
        async def complete(self, messages, tools=None, cancel=None):
            cancel.set()
            from app.brain.llm import TurnCancelled
            raise TurnCancelled()

    dm = DialogManager(CancellingLlm(), settings.scenario,
                       ToolExecutor(_one_c(), "+7000000000"))
    dm.open_greeting()
    cancel = asyncio.Event()
    result = await dm.handle_utterance("перебил", cancel)
    assert result.cancelled is True
    assert result.reply_text == ""
