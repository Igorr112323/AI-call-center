"""Offline-brain anti-loop: varied questions, smalltalk, steering, LLM nudge.

The deterministic brain must never sound like a broken record: identical
re-asks are replaced with rotated phrasings, off-script chatter gets a
natural acknowledgment, and repeated confusion produces a summary plus a
human handoff offer.
"""
import json

import pytest

from app.brain.dialog_manager import DialogManager, _ANTI_LOOP_NUDGE
from app.brain.llm import AssistantMessage, LlmClient
from app.brain.tools import ToolExecutor
from app.config import AppSettings, LlmConfig
from app.core.storage import Storage
from app.integrations.catalog_provider import LocalCatalogProvider


@pytest.fixture
def dm(tmp_path):
    settings = AppSettings.load(tmp_path / "s.json")
    storage = Storage(tmp_path / "b.db")
    storage.replace_catalog([
        {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
         "category": "актуатор", "price": 11500.0, "stock": 14,
         "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
    ])
    catalog = LocalCatalogProvider(storage, settings.catalog)
    executor = ToolExecutor(catalog, "+79182041122")
    llm = LlmClient(LlmConfig(api_key=""))  # empty key -> offline brain
    manager = DialogManager(llm, settings.scenario, executor)
    manager.open_greeting()
    return manager


_OFF_SCRIPT = (
    "какая сегодня погода?",
    "расскажи анекдот",
    "какие у вас новости?",
)


async def test_offscript_turns_never_repeat_identical_reply(dm):
    replies = []
    for phrase in _OFF_SCRIPT:
        result = await dm.handle_utterance(phrase)
        replies.append(result.reply_text)
    assert len(set(replies)) == 3  # all three answers differ


async def test_repeated_confusion_offers_summary_and_human_handoff(dm):
    first = await dm.handle_utterance(_OFF_SCRIPT[0])
    second = await dm.handle_utterance(_OFF_SCRIPT[0])  # same phrase again
    third = await dm.handle_utterance(_OFF_SCRIPT[0])

    assert first.reply_text != second.reply_text
    assert "Не хватает" in second.reply_text  # steering summary
    assert "менеджер" in third.reply_text.lower()  # handoff after 3rd miss


async def test_greeting_acknowledged_then_slot_question(dm):
    result = await dm.handle_utterance("Приветствую вас")
    assert "Здравствуйте" in result.reply_text
    assert result.reply_text.rstrip().endswith("?")


async def test_slot_questions_rotate_when_customer_keeps_off_topic(dm):
    texts = set()
    for phrase in _OFF_SCRIPT:
        texts.add((await dm.handle_utterance(phrase)).reply_text)
    # no robotic identical re-ask, every reply still drives the flow forward
    assert all("?" in t for t in texts)


async def test_thanks_acknowledged(dm):
    thanks = await dm.handle_utterance("спасибо вам большое")
    assert "Пожалуйста" in thanks.reply_text


async def test_capability_question_answered(dm):
    capability = await dm.handle_utterance("что ты вообще умеешь?")
    assert "подбираю оборудование" in capability.reply_text.lower()


async def test_confused_reply_summarises_collected_slots(dm):
    # one real parameter first, then two off-script phrases
    await dm.handle_utterance("Нужен актуатор на 24 вольта")
    await dm.handle_utterance(_OFF_SCRIPT[1])
    steering = await dm.handle_utterance(_OFF_SCRIPT[2])
    assert "напряжение" in steering.reply_text.lower()
    assert "24" in steering.reply_text


async def test_unique_reply_guard_paraphrases_even_under_duplicates(dm):
    # force the same underlying question twice via repeat requests
    r1 = await dm.handle_utterance("повтори пожалуйста")
    r2 = await dm.handle_utterance("повтори ещё раз")
    assert r1.reply_text != r2.reply_text


class _ScriptedLlm:
    def __init__(self, script):
        self.script = list(script)

    async def complete(self, messages, tools=None, cancel=None):
        if not self.script:
            return AssistantMessage(content="Что-нибудь ещё?")
        return self.script.pop(0)


async def test_cloud_llm_duplicate_reply_gets_anti_loop_nudge(dm):
    dm.llm = _ScriptedLlm([
        AssistantMessage(content="Какой у вас бюджет?"),  # turn 1
        AssistantMessage(content="Какой у вас бюджет?"),  # turn 2: verbatim repeat
    ])
    await dm.handle_utterance("привет")
    result = await dm.handle_utterance("а ещё что?")
    system_notes = [m for m in dm.messages if m.get("role") == "system"]
    assert any(_ANTI_LOOP_NUDGE in m["content"] for m in system_notes)
    assert result.reply_text == "Что-нибудь ещё?"  # model recovered after nudge
