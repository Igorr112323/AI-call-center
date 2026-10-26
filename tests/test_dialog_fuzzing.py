"""Chaos testing for the dialog engine: rude, vague and indecisive customers.

Runs the fully offline brain (real DialogManager + LocalCatalogProvider, no
LLM key, no network) against slang numerals, mid-call mind changes, repeated
objections, aggression, silence timeouts and contradictory requests. The
state machine must never raise, never get stuck and always keep a coherent
slot set.
"""
import json

import pytest

from app.brain.dialog_manager import DialogManager, DialogState
from app.brain.llm import LlmClient
from app.brain.tools import ToolExecutor
from app.config import AppSettings, CatalogConfig
from app.core.storage import Storage
from app.integrations.catalog_provider import (
    LocalCatalogProvider, extract_params,
)

_CATALOG = [
    {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
     "category": "актуатор", "price": 11500.0, "stock": 14,
     "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
    {"sku": "ACT-12-300", "name": "Актуатор линейный 12В, ход 300мм",
     "category": "актуатор", "price": 6800.0, "stock": 9,
     "params_json": json.dumps({"voltage": 12, "stroke_mm": 300})},
    {"sku": "ACT-12-150", "name": "Актуатор линейный 12В, ход 150мм",
     "category": "актуатор", "price": 4200.0, "stock": 60,
     "params_json": json.dumps({"voltage": 12, "stroke_mm": 150})},
    {"sku": "ACT-24-500", "name": "Актуатор линейный 24В, ход 500мм",
     "category": "актуатор", "price": 18000.0, "stock": 6,
     "params_json": json.dumps({"voltage": 24, "stroke_mm": 500})},
]


@pytest.fixture
def dm(tmp_path):
    settings = AppSettings.load(tmp_path / "settings.json")
    settings.sip.enabled = False
    storage = Storage(tmp_path / "fuzz.db")
    storage.replace_catalog(_CATALOG)
    catalog = LocalCatalogProvider(storage, CatalogConfig())
    llm = LlmClient(settings.llm)
    executor = ToolExecutor(catalog, "+79990007788")
    manager = DialogManager(llm, settings.scenario, executor)
    manager.open_greeting()
    return manager, executor


# ------------------------------------------------------------- speech parsing
@pytest.mark.parametrize("phrase, expected", [
    ("шток тридцатка, нужен актуатор", {"stroke_mm": 300}),
    ("ход полтора метра", {"stroke_mm": 1500}),
    ("ход два с половиной метра", {"stroke_mm": 2500}),
    ("нужна двадцатьчетверка", {"voltage": 24}),
    ("две штуки", {"quantity": 2}),
    ("Актуатор на 24 вольта, ход 300 мм", {"voltage": 24, "stroke_mm": 300}),
    ("привод на двадцать четыре вольта, ход триста миллиметров, три штуки",
     {"voltage": 24, "stroke_mm": 300, "quantity": 3}),
    ("усилие тысяча ньютонов", {"force_n": 1000}),
    ("две тысячи ньютон, пять штук", {"force_n": 2000, "quantity": 5}),
    ("ой, пересчитайте на двенадцать вольт", {"voltage": 12}),
])
def test_slang_and_spoken_numerals_parse(phrase, expected):
    params = extract_params(phrase)
    for key, value in expected.items():
        assert params.get(key) == value, (phrase, params)


@pytest.mark.parametrize("phrase", [
    "скиньте рублей пятьсот",
    "пару сотен возьмём, если цена устроит",
    "алло, вы меня слышите?",
    "спасибо, я перезвоню",
])
def test_chitchat_does_not_invent_engineering_slots(phrase):
    params = extract_params(phrase)
    assert "voltage" not in params
    assert "stroke_mm" not in params
    assert "force_n" not in params


# ------------------------------------------------------------- mind changing
async def test_mid_call_mind_change_reprices_and_never_sticks(dm):
    manager, executor = dm

    first = await manager.handle_utterance(
        "Нужен актуатор на двадцать четыре вольта, ход триста миллиметров")
    assert executor.last_product and executor.last_product.article == "ACT-24-300"
    assert "11 500" in first.reply_text

    second = await manager.handle_utterance(
        "Ой, нет, стойте, я ошибся, у нас в цеху только двенадцать вольт, "
        "пересчитайте на двенадцать")
    assert manager.slots["voltage"] == 12
    # stale quote was invalidated and a new product was quoted in the same turn
    assert executor.last_product is not None
    assert executor.last_product.article == "ACT-12-300"
    assert "6 800" in second.reply_text
    assert manager.state in (DialogState.QUOTING, DialogState.CONFIRMING)
    assert manager.state is not DialogState.CLOSED


async def test_changed_mind_after_quote_reopens_collection(dm):
    manager, executor = dm
    await manager.handle_utterance("Актуатор 24 вольта, ход 500 мм, одна штука")
    assert executor.last_product and executor.last_product.article == "ACT-24-500"

    result = await manager.handle_utterance(
        "Нет, давайте ход 150 мм и напряжение 12 вольт")
    assert manager.slots["stroke_mm"] == 150 and manager.slots["voltage"] == 12
    assert executor.last_product.article == "ACT-12-150"
    assert "4 200" in result.reply_text
    # no order can exist before explicit confirmation
    assert manager.order is None


# ------------------------------------------------------------- objections
async def test_objections_answer_differently_on_repeat(dm):
    manager, _ = dm
    first = await manager.handle_utterance("Че так дорого-то у вас?")
    second = await manager.handle_utterance("Нет, правда, дорого, у конкурентов ниже")
    assert first.reply_text and second.reply_text
    assert first.reply_text != second.reply_text
    assert manager.state is not DialogState.CLOSED


async def test_robot_and_human_escalation_objections(dm):
    manager, _ = dm
    robot = await manager.handle_utterance("Вы робот вообще?")
    human = await manager.handle_utterance("Соедините с живым человеком немедленно")
    assert "ассистент" in robot.reply_text.lower() or "менеджер" in robot.reply_text.lower()
    assert "менеджер" in human.reply_text.lower()
    # second identical escalation must not repeat the first line verbatim
    again = await manager.handle_utterance("Я сказал, соедините с человеком!")
    assert again.reply_text != human.reply_text


async def test_objection_then_sale_recovers_naturally(dm):
    manager, executor = dm
    await manager.handle_utterance("А скидку дадите вообще?")
    await manager.handle_utterance("Я подумаю над вашим предложением")
    quote = await manager.handle_utterance(
        "Ладно, давайте актуатор 24 вольта, ход 300 мм")
    assert executor.last_product and executor.last_product.found
    assert "11 500" in quote.reply_text
    order = await manager.handle_utterance("Да, оформляем заказ")
    assert manager.state is DialogState.ORDER_CREATED
    assert order.order is not None


# ------------------------------------------------------------- silence
async def test_silence_watchdog_three_strikes(dm):
    manager, _ = dm
    first = await manager.handle_silence()
    second = await manager.handle_silence()
    assert not first.silence_timeout and not second.silence_timeout
    assert "не слышно" in first.reply_text.lower()
    third = await manager.handle_silence()
    assert third.silence_timeout
    assert manager.state is DialogState.CLOSED


async def test_real_utterance_resets_silence_counter(dm):
    manager, _ = dm
    await manager.handle_silence()
    await manager.handle_utterance("Да, я здесь, актуатор нужен")
    for _ in range(2):
        result = await manager.handle_silence()
        assert not result.silence_timeout
    assert manager.state is not DialogState.CLOSED


# ------------------------------------------------------------- order guards
async def test_no_duplicate_order_on_repeated_confirmation(dm):
    manager, executor = dm
    await manager.handle_utterance("Актуатор 24 вольта, ход 300 мм, одна штука")
    await manager.handle_utterance("Да, оформляем")
    assert manager.state is DialogState.ORDER_CREATED
    number = manager.order.number_1c if manager.order else None

    repeat = await manager.handle_utterance("Да, да, оформляем же!")
    assert repeat.order is not None
    # executor did not run create_order a second time -> no duplicate lead
    assert (manager.order.number_1c if manager.order else None) == number
    assert "уже зафиксирован" in repeat.reply_text.lower()


# ------------------------------------------------------------- chaos loop
async def test_randomized_chaos_never_breaks_state_machine(dm):
    manager, executor = dm
    chaos = [
        "алло?", "что вы там бормочете", "нужен актуатор",
        "двадцать четыре вольта", "ой нет, двенадцать вольт",
        "че так дорого", "ход триста мм", "а скидку дадите",
        "вы робот?", "ладно, две штуки", "соедините с человеком",
        "я подумаю", "да оформляем уже", "стоп, а на 24 вольта можно?",
        "спасибо", "",
    ]
    for phrase in chaos:
        if not phrase.strip():
            result = await manager.handle_silence()
        else:
            result = await manager.handle_utterance(phrase)
        assert isinstance(result.state, DialogState)
        assert result.reply_text is not None
        for key in ("voltage", "stroke_mm", "quantity"):
            if key in manager.slots:
                assert manager.slots[key] is not None
    # manager remains usable or cleanly closed; no exception escaped
    assert manager.state in set(DialogState)
