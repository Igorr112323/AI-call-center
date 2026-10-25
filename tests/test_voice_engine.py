import asyncio

import pytest

from app.config import TtsConfig, VadConfig
from app.voice.stt import MockSTT
from app.voice.tts import MockTts
from app.voice.vad import FRAME_MS
from app.voice.voice_engine import VirtualTransport, VoiceEngine
from conftest import silence_pcm, tone_pcm


def _vad():
    return VadConfig(provider="energy", energy_threshold_db=-45,
                     min_speech_ms=240, min_silence_ms=300,
                     barge_in=True, preroll_ms=100)


def _frame_stream(pcm: bytes, sample_rate: int = 16000) -> list[bytes]:
    size = int(sample_rate * 2 * FRAME_MS / 1000)
    return [pcm[i:i + size] for i in range(0, len(pcm) - size + 1, size)]


class GatedTransport(VirtualTransport):
    """Playback blocks until the gate opens, keeping the bot 'speaking' on demand."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()

    async def play(self, pcm16: bytes) -> None:
        await self.gate.wait()
        await super().play(pcm16)


async def test_end_to_end_pipeline_recognizes_and_replies(tmp_path):
    replies = []

    async def on_user_text(text: str) -> str:
        replies.append(text)
        return "Одну секунду, проверяю."

    events = []
    engine = VoiceEngine(
        MockSTT(["Нужен актуатор на 24 вольта"]),
        MockTts(TtsConfig(sample_rate=16000)),
        _vad(), TtsConfig(sample_rate=16000),
        VirtualTransport(), on_user_text,
        on_event=lambda e: events.append(e),
        record_path=tmp_path / "call.wav",
    )
    await engine.start()
    try:
        for frame in _frame_stream(tone_pcm(500)):
            engine.ingest_frame(frame)
        for frame in _frame_stream(silence_pcm(500)):
            engine.ingest_frame(frame)

        deadline = asyncio.get_running_loop().time() + 3
        while (not replies or engine.last_latency_ms == 0) \
                and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert replies == ["Нужен актуатор на 24 вольта"]
        assert engine.transport.played_total_bytes > 0
        assert any(e["type"] == "speech_started" for e in events)
        assert engine.last_latency_ms > 0
    finally:
        path = await engine.stop()

    assert path is not None and path.exists()


async def test_barge_in_cancels_playback(tmp_path):
    async def on_user_text(text):
        return ""

    transport = GatedTransport()
    engine = VoiceEngine(
        MockSTT(), MockTts(TtsConfig(sample_rate=16000)),
        _vad(), TtsConfig(sample_rate=16000),
        transport, on_user_text,
        record_path=tmp_path / "barge.wav",
    )
    await engine.start()
    try:
        speak_task = asyncio.create_task(engine.speak("Длинная фраза бота на несколько слов."))
        await asyncio.sleep(0.1)
        assert engine.bot_speaking is True

        # caller starts talking mid-reply
        for frame in _frame_stream(tone_pcm(400)):
            engine.ingest_frame(frame)

        deadline = asyncio.get_running_loop().time() + 2
        while not engine._cancel.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert engine._cancel.is_set()

        transport.gate.set()  # unblock playback; cancellation wins anyway
        await speak_task
        assert engine.bot_speaking is False
    finally:
        await engine.stop()


async def test_filler_is_played_when_idle(tmp_path):
    async def on_user_text(text):
        return ""

    transport = VirtualTransport()
    engine = VoiceEngine(
        MockSTT(), MockTts(TtsConfig(sample_rate=16000)),
        _vad(), TtsConfig(sample_rate=16000, fillers=["Секунду."]),
        transport, on_user_text,
        record_path=tmp_path / "filler.wav",
    )
    await engine.start()
    try:
        await engine.speak_filler()
        assert transport.played_total_bytes > 0
    finally:
        await engine.stop()
