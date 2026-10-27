"""Edge online neural TTS: stream parsing, rate mapping, soft failure.

The network service is never called: a fake ``edge_tts`` package streams
in-memory chunks; MP3 decoding is exercised separately with a real file.
"""
import asyncio
import sys
import types

import pytest

from app.config import TtsConfig
from app.voice.edge_tts_provider import EdgeTts, EdgeUnavailable, FEMALE_RU


class _FakeCommunicate:
    instances: list = []

    def __init__(self, text, voice, rate="+0%", **kwargs):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.kwargs = kwargs
        _FakeCommunicate.instances.append(self)

    async def stream(self):
        yield {"type": "SentenceBoundary", "data": b"meta"}
        yield {"type": "audio", "data": b"MP3PART1-"}
        yield {"type": "WordBoundary", "data": b"meta"}
        yield {"type": "audio", "data": b"MP3PART2"}


def _install_fake_edge(monkeypatch, *, communicate_cls=_FakeCommunicate):
    module = types.ModuleType("edge_tts")
    module.Communicate = communicate_cls
    monkeypatch.setitem(sys.modules, "edge_tts", module)


def test_default_voice_is_russian_svetlana():
    tts = EdgeTts(TtsConfig())
    assert tts.voice == FEMALE_RU == "ru-RU-SvetlanaNeural"
    # explicit ru-RU voice id is honoured
    custom = EdgeTts(TtsConfig(voice="ru-RU-DmitryNeural"))
    assert custom.voice == "ru-RU-DmitryNeural"


@pytest.mark.parametrize("speed,expected", [
    (1.0, "+0%"), (1.5, "+50%"), (2.0, "+100%"), (0.5, "-50%"),
    (3.0, "+100%"), (0.1, "-50%"),
])
def test_rate_mapping(speed, expected):
    assert EdgeTts(TtsConfig(speed=speed))._rate() == expected


def test_synthesize_collects_audio_chunks_and_decodes(monkeypatch):
    _install_fake_edge(monkeypatch)
    _FakeCommunicate.instances.clear()
    tts = EdgeTts(TtsConfig(sample_rate=24000, speed=1.25))

    captured = {}

    def fake_decode(mp3_bytes):
        captured["mp3"] = mp3_bytes
        return b"PCM!" * 100

    monkeypatch.setattr(tts, "_decode_mp3", fake_decode)
    pcm = asyncio.run(tts.synthesize("Добрый день"))

    assert pcm == b"PCM!" * 100
    assert captured["mp3"] == b"MP3PART1-MP3PART2"  # metadata skipped
    used = _FakeCommunicate.instances[0]
    assert used.voice == FEMALE_RU and used.rate == "+25%"


def test_empty_text_short_circuits(monkeypatch):
    _install_fake_edge(monkeypatch)
    _FakeCommunicate.instances.clear()
    tts = EdgeTts(TtsConfig())
    assert asyncio.run(tts.synthesize("   ")) == b""
    assert _FakeCommunicate.instances == []


def test_missing_package_raises(monkeypatch):
    # Works even when real edge-tts IS installed: a module without the
    # Communicate symbol makes the constructor access fail deterministically.
    broken = types.ModuleType("edge_tts")
    monkeypatch.setitem(sys.modules, "edge_tts", broken)
    tts = EdgeTts(TtsConfig())
    with pytest.raises(EdgeUnavailable):
        asyncio.run(tts.synthesize("Текст"))


def test_service_error_is_wrapped(monkeypatch):
    class _Boom:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            raise OSError("websocket down")
            yield  # pragma: no cover - makes this an async generator

    _install_fake_edge(monkeypatch, communicate_cls=_Boom)
    tts = EdgeTts(TtsConfig())
    with pytest.raises(EdgeUnavailable, match="service failed"):
        asyncio.run(tts.synthesize("Текст"))


def test_empty_audio_stream_raises(monkeypatch):
    class _Silent:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            yield {"type": "WordBoundary", "data": b"only metadata"}

    _install_fake_edge(monkeypatch, communicate_cls=_Silent)
    tts = EdgeTts(TtsConfig())
    with pytest.raises(EdgeUnavailable, match="no audio"):
        asyncio.run(tts.synthesize("Текст"))


def test_real_mp3_decodes_to_resampled_pcm():
    """End-to-end decode through bundled libsndfile; skips if MP3 absent."""
    soundfile = pytest.importorskip("soundfile")
    if "MP3" not in soundfile.available_formats():
        pytest.skip("libsndfile without MP3 support")
    import io

    import numpy as np

    sr = 24000
    t = np.linspace(0, 0.2, int(sr * 0.2), endpoint=False)
    wave = (0.2 * 32767 * np.sin(2 * np.pi * 220 * t)).astype("<i2")
    buf = io.BytesIO()
    soundfile.write(buf, wave, sr, format="MP3")
    tts = EdgeTts(TtsConfig(sample_rate=16000))
    pcm = tts._decode_mp3(buf.getvalue())
    # resampled 0.2 s of 16-bit mono, tolerance for codec framing
    assert len(pcm) == pytest.approx(0.2 * 16000 * 2, rel=0.1)
