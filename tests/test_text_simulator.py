"""Text call simulator: full keyboard-driven sale through the production brain.

Covers the TextSimulatorAgent lifecycle, RTT/tool badge payloads, SQLite
persistence with the 'text' source marker, PDF quote generation and mute-safe
behaviour — no microphone or audio hardware required.
"""
import json
from pathlib import Path

import pytest

from app.config import AppSettings
from app.core.engine import CallEngine
from app.core.models import CallStatus, Speaker
from app.core.storage import Storage
from app.telephony.text_simulator import (
    SpeakerPlayer, TextSimulatorAgent, VIRTUAL_PHONE,
)


class _NullPlayer(SpeakerPlayer):
    """Deterministic stand-in: never touches PortAudio."""

    @property
    def available(self) -> bool:
        return False

    def play(self, pcm: bytes, sample_rate: int) -> None:
        self.played = (pcm, sample_rate)

    def stop(self) -> None:
        pass


def _build(tmp_path: Path) -> tuple[AppSettings, Storage, CallEngine, TextSimulatorAgent]:
    settings = AppSettings.load(tmp_path / "settings.json")
    settings.sip.enabled = False
    settings.stt.provider = "mock"
    settings.tts.provider = "mock"
    settings.tts.sample_rate = 16000
    settings.llm.base_url = "http://127.0.0.1:9"
    settings.llm.timeout_ms = 300

    storage = Storage(tmp_path / "text_sim.db")
    storage.replace_catalog([
        {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
         "category": "актуатор", "price": 11500.0, "stock": 14,
         "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
    ])
    engine = CallEngine(settings, storage, tmp_path)
    agent = TextSimulatorAgent(engine, settings, player=_NullPlayer())
    return settings, storage, engine, agent


async def test_text_sale_full_flow(tmp_path):
    settings, storage, engine, agent = _build(tmp_path)
    assert agent.muted is True  # null player -> automatic mute
    assert VIRTUAL_PHONE.endswith("TEST")

    info = await agent.start()
    call_id = info["call_id"]
    assert agent.active and call_id > 0
    # open_greeting() hands the turn over to slot collection immediately
    assert info["greeting"] and info["state"] == "collecting"

    row = storage.get_call(call_id)
    assert row.phone == VIRTUAL_PHONE
    assert row.source == "text"
    assert row.record_path is None
    assert row.transcript[0].speaker is Speaker.AGENT

    turn1 = await agent.send_user_utterance(
        "Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
    assert turn1.reply and "11 500" in turn1.reply
    assert turn1.rtt_ms >= 1
    assert "check_catalog_and_price" in turn1.tool_names
    assert turn1.product and turn1.product["found"] is True
    assert turn1.product["article"] == "ACT-24-300"
    assert turn1.slots.get("voltage") == 24
    assert turn1.slots.get("stroke_mm") == 300
    assert turn1.spoken is False  # muted -> no playback attempted

    turn2 = await agent.send_user_utterance("Да, оформляем заказ")
    assert turn2.order is not None
    assert turn2.state == "order_created"

    result = await agent.hangup()
    assert agent.active is False
    assert result["status"] == CallStatus.ORDER.value
    assert result["pdf"] and Path(result["pdf"]).exists()

    finished = storage.get_call(call_id)
    assert finished.status is CallStatus.ORDER
    assert finished.source == "text"
    assert finished.order is not None and finished.order.items
    assert finished.order.total == pytest.approx(11500.0)
    speakers = [line.speaker for line in finished.transcript]
    assert speakers[0] is Speaker.AGENT            # greeting
    assert speakers.count(Speaker.CLIENT) == 2
    assert speakers.count(Speaker.AGENT) == 3

    # on-demand PDF regeneration for the inspector 'open quote' action
    again = await engine.quote_pdf_path(finished.order.id)
    assert again.endswith(".pdf") and Path(again).exists()

    # engine is reusable for a second session after hangup
    next_info = await engine.start_text_call(VIRTUAL_PHONE)
    assert next_info["call_id"] != call_id
    await engine.end_text_call()

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()


async def test_double_start_rejected_and_session_guards(tmp_path):
    _, storage, engine, agent = await _started_agent(tmp_path)
    with pytest.raises(RuntimeError):
        await engine.start_text_call(VIRTUAL_PHONE)

    await agent.hangup()
    with pytest.raises(RuntimeError):
        await agent.send_user_utterance("алло")
    with pytest.raises(RuntimeError):
        await agent.hangup()

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()


async def test_empty_utterance_rejected(tmp_path):
    _, storage, engine, agent = await _started_agent(tmp_path)
    with pytest.raises(ValueError):
        await agent.send_user_utterance("   ")
    await agent.hangup()

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()


async def test_mute_toggle_and_tts_failure_is_safe(tmp_path):
    settings, storage, engine, agent = _build(tmp_path)

    class _FlakyPlayer(_NullPlayer):
        @property
        def available(self) -> bool:
            return True

        def play(self, pcm, sample_rate):
            raise RuntimeError("device lost")

    agent.player = _FlakyPlayer()
    agent.set_muted(False)
    assert agent.muted is False
    await agent.start()
    event = await agent.send_user_utterance("Здравствуйте")
    # playback failure must not break the turn; the reply still arrives
    assert event.reply and event.spoken is False

    agent.set_muted(True)
    event2 = await agent.send_user_utterance("Нужен актуатор")
    assert event2.spoken is False
    await agent.hangup()

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()


async def test_short_text_call_without_order_is_consultation(tmp_path):
    _, storage, engine, agent = await _started_agent(tmp_path)
    # enough chatter to clear the 5-second failed-call threshold is not
    # guaranteed, so accept both terminal non-order statuses but never 'order'
    result = await agent.hangup()
    assert result["status"] in (CallStatus.CONSULTATION.value, CallStatus.FAILED.value)
    assert result["pdf"] is None
    assert storage.get_call(result["call_id"]).order is None

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()


async def _started_agent(tmp_path):
    settings, storage, engine, agent = _build(tmp_path)
    await agent.start()
    return settings, storage, engine, agent
