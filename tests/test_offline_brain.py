"""End-to-end selling flow with zero external dependencies:

real LlmClient without API key -> offline rule brain -> local SQLite catalog
-> local order numbering. This is what showroom demos run on.
"""
import json

import pytest

from app.brain.dialog_manager import DialogManager, DialogState
from app.brain.llm import LlmClient
from app.brain.tools import ToolExecutor
from app.config import AppSettings, LlmConfig
from app.core.storage import Storage
from app.integrations.catalog_provider import LocalCatalogProvider


@pytest.fixture
def brain(tmp_path):
    settings = AppSettings.load(tmp_path / "s.json")
    assert settings.llm.api_key == ""
    storage = Storage(tmp_path / "b.db")
    storage.replace_catalog([
        {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
         "category": "актуатор", "price": 11500.0, "stock": 14,
         "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
    ])
    catalog = LocalCatalogProvider(storage, settings.catalog)
    executor = ToolExecutor(catalog, "+79182041122")
    llm = LlmClient(LlmConfig(api_key=""))  # empty key -> offline mode
    return DialogManager(llm, settings.scenario, executor), executor


async def test_offline_quote_and_order(brain):
    dm, executor = brain
    assert dm.open_greeting()

    r1 = await dm.handle_utterance(
        "Здравствуйте, нужен актуатор на 24 вольта, ход штока 300 мм, одна штука")
    assert executor.last_product and executor.last_product.found
    assert executor.last_product.article == "ACT-24-300"
    assert "11 500" in r1.reply_text
    assert r1.state in (DialogState.QUOTING, DialogState.CONFIRMING)

    r2 = await dm.handle_utterance("Да, оформляем заказ")
    assert r2.state is DialogState.ORDER_CREATED
    assert r2.order is not None and r2.order.total == 11500
    assert executor.order_result["standalone"] is True
    assert executor.order_result["number_1c"].startswith("ЛОКАЛ-")


async def test_offline_objection_playbook(brain):
    dm, _ = brain
    await dm.handle_utterance("Это слишком дорого для нас")
    # objection rule text comes straight from the configured playbook
    assert "аналог" in dm.messages[-1]["content"].lower()


async def test_unknown_product_offline_reply(brain):
    dm, _ = brain
    result = await dm.handle_utterance(
        "Нужен актуатор на 380 вольт, ход 900 мм")
    # no matching catalog row -> graceful "not found" wording, no crash
    assert result.reply_text
    assert "не найдена" in result.reply_text.lower() or "аналог" in result.reply_text.lower()
