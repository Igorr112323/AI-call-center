"""Piper neural TTS: model resolution, synthesis, speed mapping, failover.

The real piper/onnxruntime stack is never imported here: a fake ``piper``
package is injected into sys.modules and writes a real WAV through the
same wave.Wave_write contract the engine uses.
"""
import asyncio
import math
import struct
import sys
import types
import wave
from pathlib import Path

import pytest

from app.config import TtsConfig
from app.voice import piper_tts
from app.voice.piper_tts import (
    MODEL_FILE,
    PiperTts,
    PiperUnavailable,
    piper_model_paths,
)
from app.voice.tts import ChainedTts, MockTts, build_tts


def _tone_wav_bytes(sample_rate: int = 22050, duration: float = 0.2) -> bytes:
    frames = bytearray()
    for i in range(int(sample_rate * duration)):
        sample = int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / sample_rate))
        frames += struct.pack("<h", sample)
    return bytes(frames)


class _FakePiperVoice:
    instances: list = []
    last_length_scale = None

    def __init__(self):
        self.calls = []
        _FakePiperVoice.instances.append(self)

    def synthesize(self, text, wav_writer, length_scale=None, **kwargs):
        self.calls.append((text, length_scale))
        _FakePiperVoice.last_length_scale = length_scale
        wav_writer.setframerate(22050)
        wav_writer.setsampwidth(2)
        wav_writer.setnchannels(1)
        wav_writer.writeframes(_tone_wav_bytes())


def _install_fake_piper(monkeypatch, *, load_error=None):
    module = types.ModuleType("piper")

    class _Voice:
        @staticmethod
        def load(model_path, config_path=None):
            if load_error is not None:
                raise load_error
            assert Path(model_path).exists() and Path(config_path).exists()
            return _FakePiperVoice()

    module.PiperVoice = _Voice
    monkeypatch.setitem(sys.modules, "piper", module)


def _fake_model_files(monkeypatch, tmp_path):
    model = tmp_path / MODEL_FILE
    config = tmp_path / f"{MODEL_FILE}.json"
    model.write_bytes(b"ONNXMODEL")
    config.write_text('{"audio": {"sample_rate": 22050}}', encoding="utf-8")
    monkeypatch.setattr(
        piper_tts, "piper_model_paths", lambda: (model, config))
    return model, config


def test_piper_model_paths_absent_in_clean_checkout():
    # source checkout without a downloaded model resolves to None
    assert piper_model_paths() is None


def test_synthesize_decodes_and_resamples_neural_wav(monkeypatch, tmp_path):
    _install_fake_piper(monkeypatch)
    _fake_model_files(monkeypatch, tmp_path)
    _FakePiperVoice.instances.clear()

    tts = PiperTts(TtsConfig(provider="piper", sample_rate=24000, speed=1.0))
    pcm = asyncio.run(tts.synthesize("Здравствуйте, это нейросетевой голос"))

    # 0.2 s at 22.05 kHz resampled to 24 kHz, 16-bit mono
    assert len(pcm) == pytest.approx(0.2 * 24000 * 2, rel=0.05)
    voice = _FakePiperVoice.instances[0]
    assert voice.calls[0][0].startswith("Здравствуйте")
    assert voice.calls[0][1] == 1.0  # default length scale


def test_speed_knob_maps_to_inverse_length_scale(monkeypatch, tmp_path):
    _install_fake_piper(monkeypatch)
    _fake_model_files(monkeypatch, tmp_path)

    for speed, expected in ((1.0, 1.0), (2.0, 0.6), (0.5, 1.8)):
        tts = PiperTts(TtsConfig(sample_rate=16000, speed=speed))
        asyncio.run(tts.synthesize("Проверка темпа"))
        assert _FakePiperVoice.last_length_scale == pytest.approx(expected)


def test_empty_text_short_circuits_without_loading(monkeypatch, tmp_path):
    _install_fake_piper(monkeypatch)
    _fake_model_files(monkeypatch, tmp_path)
    _FakePiperVoice.instances.clear()
    tts = PiperTts(TtsConfig())
    assert asyncio.run(tts.synthesize("   ")) == b""
    assert _FakePiperVoice.instances == []


def test_missing_model_raises_and_marks_failed(monkeypatch):
    monkeypatch.setattr(piper_tts, "piper_model_paths", lambda: None)
    tts = PiperTts(TtsConfig())
    with pytest.raises(PiperUnavailable, match="not found"):
        tts._synthesize_blocking("Тихо")
    assert tts._load_failed is True
    # second attempt fails fast without re-trying
    with pytest.raises(PiperUnavailable):
        tts._synthesize_blocking("Ещё раз")


def test_engine_import_failure_is_wrapped(monkeypatch, tmp_path):
    _fake_model_files(monkeypatch, tmp_path)
    # Works on hosts where real piper-tts IS installed too: a module without
    # the PiperVoice symbol makes "from piper import PiperVoice" raise.
    broken = types.ModuleType("piper")
    monkeypatch.setitem(sys.modules, "piper", broken)
    tts = PiperTts(TtsConfig())
    with pytest.raises(PiperUnavailable, match="piper package unavailable"):
        tts._synthesize_blocking("Сбой импорта")


def test_model_load_error_is_wrapped(monkeypatch, tmp_path):
    _install_fake_piper(monkeypatch, load_error=RuntimeError("bad onnx"))
    _fake_model_files(monkeypatch, tmp_path)
    tts = PiperTts(TtsConfig())
    with pytest.raises(PiperUnavailable, match="model load failed"):
        tts._synthesize_blocking("Сбой модели")


# ------------------------------------------------------------- failover chain

class _ExplodingTts:
    sample_rate = 16000

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        raise RuntimeError("boom")

    async def aclose(self):
        pass


class _StaticTts:
    sample_rate = 16000

    def __init__(self, payload=b"pcm-data"):
        self.payload = payload
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        return self.payload

    async def aclose(self):
        pass


def test_chained_tts_fails_over_and_retires_dead_provider():
    bad = _ExplodingTts()
    good = _StaticTts()
    chain = ChainedTts([bad, good], sample_rate=16000)

    assert asyncio.run(chain.synthesize("Первая фраза")) == b"pcm-data"
    assert asyncio.run(chain.synthesize("Вторая фраза")) == b"pcm-data"
    assert bad.calls == 1   # retired after the single failure
    assert good.calls == 2


def test_chained_tts_all_dead_returns_silence():
    chain = ChainedTts([_ExplodingTts()], sample_rate=16000)
    assert asyncio.run(chain.synthesize("Тишина")) == b"\x00\x00" * (16000 // 4)


def test_chained_tts_empty_text():
    good = _StaticTts()
    chain = ChainedTts([good], sample_rate=16000)
    assert asyncio.run(chain.synthesize("  ")) == b""
    assert good.calls == 0


# ------------------------------------------------------------------- build_tts

def test_build_auto_includes_piper_when_model_bundled(monkeypatch):
    monkeypatch.setattr("app.voice.tts.is_piper_available", lambda: True)
    provider = build_tts(TtsConfig(provider="auto"))
    assert isinstance(provider, ChainedTts)
    assert any(isinstance(p, PiperTts) for p in provider.providers)
    assert any(isinstance(p, MockTts) for p in provider.providers)


def test_build_auto_without_piper_skips_it(monkeypatch):
    monkeypatch.setattr("app.voice.tts.is_piper_available", lambda: False)
    provider = build_tts(TtsConfig(provider="auto"))
    if isinstance(provider, ChainedTts):
        assert not any(isinstance(p, PiperTts) for p in provider.providers)
    else:
        assert isinstance(provider, MockTts)  # non-Windows host


def test_explicit_mock_is_not_chained():
    assert isinstance(build_tts(TtsConfig(provider="mock")), MockTts)


def test_explicit_piper_without_model_degrades_safely(monkeypatch):
    monkeypatch.setattr("app.voice.tts.is_piper_available", lambda: False)
    provider = build_tts(TtsConfig(provider="piper"))
    assert not isinstance(provider, PiperTts)
    if isinstance(provider, ChainedTts):
        assert not any(isinstance(p, PiperTts) for p in provider.providers)
