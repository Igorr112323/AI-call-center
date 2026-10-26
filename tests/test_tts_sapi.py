"""Windows SAPI offline TTS: command building, WAV decoding and soft failure.

Runs on any OS: PowerShell is never executed, subprocess.run is stubbed and
the WAV it would have produced is generated with the stdlib wave module.
"""
import base64
import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app.config import AppSettings, TtsConfig
from app.voice.tts import MockTts, SapiTts, build_tts


def _write_wav(path: str, sample_rate: int, channels: int,
               duration_sec: float = 0.3) -> None:
    n = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / sample_rate))
            for _ in range(channels):
                frames += struct.pack("<h", sample)
        wf.writeframes(bytes(frames))


def test_powershell_command_is_utf16_encoded_and_carries_text():
    command = SapiTts._powershell_command("Привет, как дела?", r"C:\temp\out.wav", 2)
    decoded = base64.b64decode(command).decode("utf-16-le")
    assert "SetOutputToWaveFile" in decoded
    assert "System.Speech" in decoded
    assert "$s.Rate = 2" in decoded
    # Cyrillic survives the transport untouched
    text_b64 = base64.b64encode("Привет, как дела?".encode("utf-8")).decode()
    path_b64 = base64.b64encode(r"C:\temp\out.wav".encode("utf-8")).decode()
    assert text_b64 in decoded and path_b64 in decoded
    # prefers an installed Russian voice when one exists
    assert "ru*" in decoded


def test_synthesize_decodes_and_resamples_wav(tmp_path, monkeypatch):
    cfg = TtsConfig(provider="sapi", sample_rate=24000)
    tts = SapiTts(cfg)

    def fake_run(command, **kwargs):
        wav_path = command[command.index("-EncodedCommand") + 1]
        script = base64.b64decode(wav_path).decode("utf-16-le")
        embedded = [ln for ln in script.splitlines() if "$out = " in ln][0]
        out_b64 = embedded.split("FromBase64String('")[1].split("')")[0]
        out_path = base64.b64decode(out_b64).decode("utf-8")
        _write_wav(out_path, sample_rate=16000, channels=1)

    monkeypatch.setattr("app.voice.tts.subprocess.run", fake_run)
    pcm = tts._synthesize_blocking("Проверка связи")
    # 0.3 s of source audio resampled to 24 kHz 16-bit mono
    assert len(pcm) == pytest.approx(0.3 * 24000 * 2, rel=0.05)


def test_synthesize_downmixes_stereo(tmp_path, monkeypatch):
    cfg = TtsConfig(provider="sapi", sample_rate=24000)
    tts = SapiTts(cfg)

    def fake_run(command, **kwargs):
        wav_arg = command[command.index("-EncodedCommand") + 1]
        script = base64.b64decode(wav_arg).decode("utf-16-le")
        out_b64 = [ln for ln in script.splitlines() if "$out = " in ln][0] \
            .split("FromBase64String('")[1].split("')")[0]
        _write_wav(base64.b64decode(out_b64).decode("utf-8"),
                   sample_rate=22050, channels=2)

    monkeypatch.setattr("app.voice.tts.subprocess.run", fake_run)
    pcm = tts._synthesize_blocking("Стерео в моно")
    assert pcm  # non-empty mono PCM, no exception on stereo input


def test_synthesis_failure_returns_silence_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("powershell missing")

    monkeypatch.setattr("app.voice.tts.subprocess.run", boom)
    tts = SapiTts(TtsConfig(sample_rate=16000))
    pcm = tts._synthesize_blocking("Тихий сбой")
    assert pcm == b"\x00\x00" * (16000 // 4)


async def test_empty_text_short_circuits():
    tts = SapiTts(TtsConfig())
    assert await tts.synthesize("   ") == b""


def test_build_tts_falls_back_to_mock_off_windows():
    # Linux/macOS hosts cannot run SAPI; auto and sapi both degrade to the tone
    import sys
    if sys.platform == "win32":
        pytest.skip("platform fallback behaviour is non-Windows specific")
    assert isinstance(build_tts(TtsConfig(provider="auto")), MockTts)
    assert isinstance(build_tts(TtsConfig(provider="sapi")), MockTts)
    assert isinstance(build_tts(TtsConfig(provider="mock")), MockTts)


def test_default_provider_is_auto_and_legacy_mock_migrates(tmp_path):
    settings_file = tmp_path / "settings.json"
    fresh = AppSettings.load(settings_file)
    assert fresh.tts.provider == "auto"

    legacy = {
        "tts": {
            "provider": "mock", "api_key": "", "folder_id": "", "voice": "alena",
            "speed": 1.0, "emotion": "neutral", "sample_rate": 24000,
            "fillers": ["Так, секунду."],
        }
    }
    settings_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    migrated = AppSettings.load(settings_file)
    assert migrated.tts.provider == "auto"

    # explicit non-legacy engines are preserved untouched
    legacy["tts"]["provider"] = "yandex_v3"
    settings_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    assert AppSettings.load(settings_file).tts.provider == "yandex_v3"
