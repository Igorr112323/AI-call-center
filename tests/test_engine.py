"""Full CallEngine orchestration in mock mode (no SIP, unreachable LLM).

Verifies call lifecycle, transcript persistence, fallback dialogue, recording
and status derivation end-to-end.
"""
from pathlib import Path

import pytest

from app.config import AppSettings
from app.core.engine import CallEngine
from app.core.models import CallStatus
from app.core.storage import Storage


async def test_call_lifecycle_with_fallback_brain(tmp_path):
    settings = AppSettings.load(tmp_path / "settings.json")
    settings.stt.provider = "mock"
    settings.tts.provider = "mock"
    settings.tts.sample_rate = 16000
    settings.sip.enabled = False
    # closed local port -> instant connection refusal instead of a 4s timeout
    settings.llm.base_url = "http://127.0.0.1:9"
    settings.llm.timeout_ms = 300

    storage = Storage(tmp_path / "agent.db")
    engine = CallEngine(settings, storage, tmp_path)
    events = []
    engine.add_listener(lambda e: events.append(e))

    call_id = await engine.start_call("+79182041122")
    assert any(e["type"] == "call_started" for e in events)

    # LLM unreachable -> deterministic slot question, conversation still moves
    result = await engine.simulate_utterance("Здравствуйте, нужен актуатор")
    assert result  # fallback reply text from the scenario playbook

    await engine.end_call()
    ended = next(e for e in events if e["type"] == "call_ended")
    assert ended["status"] in (CallStatus.CONSULTATION.value, CallStatus.FAILED.value)

    call = storage.get_call(call_id)
    assert call is not None
    assert len(call.transcript) >= 3  # greeting + client + agent fallback
    assert call.transcript[0].speaker.value == "agent"
    assert call.record_path and Path(call.record_path).exists()
    assert call.duration_sec >= 0

    await engine.one_c.aclose()
    await engine.llm.aclose()
    storage.close()
