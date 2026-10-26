"""Windows SAPI offline TTS: command building, WAV decoding and soft failure.

Runs on any OS: neither PowerShell nor COM is really executed. subprocess.run
is stubbed and the comtypes package is replaced with an in-memory fake; all
WAV payloads are generated with the stdlib wave module.
"""
import base64
import io
import json
import math
import struct
import sys
import tempfile
import types
import wave
from pathlib import Path

import pytest

from app.config import AppSettings, TtsConfig
from app.voice import sapi_com
from app.voice.sapi_com import NativeSapiError, NativeSapiRenderer, decode_wav
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
    tts._native_disabled = True  # isolate the PowerShell fallback path
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
    tts._native_disabled = True  # isolate the PowerShell fallback path
    pcm = tts._synthesize_blocking("Стерео в моно")
    assert pcm  # non-empty mono PCM, no exception on stereo input


def test_synthesis_failure_returns_silence_instead_of_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("powershell missing")

    monkeypatch.setattr("app.voice.tts.subprocess.run", boom)
    tts = SapiTts(TtsConfig(sample_rate=16000))
    tts._native_disabled = True  # isolate the PowerShell fallback path
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


# ---------------------------------------------------------------- in-process COM

def _wav_bytes(sample_rate: int = 22050, channels: int = 1,
               duration_sec: float = 0.25) -> bytes:
    buf = io.BytesIO()
    n = int(sample_rate * duration_sec)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / sample_rate))
            frames += struct.pack("<h", sample) * channels
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class _FakeTokens:
    def __init__(self, count: int):
        self.Count = count

    def Item(self, index):
        return object()


class _FakeVoice:
    def __init__(self, russian_voices: int = 1, setvoice_raises: bool = False):
        self.russian_voices = russian_voices
        self._setvoice_raises = setvoice_raises
        self.Rate = None
        self.Volume = None
        self.AudioOutputStream = None
        self.Voice = None
        self.setvoice_calls = []
        self.setoutput_calls = []
        self.calls = []

    def GetVoices(self, attribute):
        assert attribute == "Language=419"
        return _FakeTokens(self.russian_voices)

    def SetVoice(self, token):
        if self._setvoice_raises:
            raise OSError("SetVoice unsupported")
        self.setvoice_calls.append(token)

    def SetOutput(self, stream, allow_format_changes):
        self.setoutput_calls.append((stream, allow_format_changes))
        self.AudioOutputStream = stream

    def Speak(self, text, flags):
        self.calls.append((text, flags))


class _FakeAudioFormat:
    def __init__(self):
        self.Type = None


class _FakeFileStream:
    """Models SpFileStream: Open writes the WAV, Close finalises it."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.Format = None
        self.opened = None
        self.closed = 0
        self.order = []

    def Open(self, path, mode):
        self.order.append("open")
        self.opened = (path, mode)
        Path(path).write_bytes(self._payload)

    def Close(self):
        self.order.append("close")
        self.closed += 1


def _install_fake_comtypes(monkeypatch, *, russian_voices=1, payload=None,
                           create_error=None, setvoice_raises=False,
                           stream_open_raises=False):
    payload = _wav_bytes() if payload is None else payload
    state = {"voice": None, "streams": [], "formats": [], "coinit": 0}

    com = types.ModuleType("comtypes")

    def _coinit():
        state["coinit"] += 1

    com.CoInitialize = _coinit
    client = types.ModuleType("comtypes.client")

    def _create(progid, dynamic=False):
        assert dynamic is True  # late binding mandatory for frozen bundles
        if create_error is not None:
            raise create_error
        if progid == "SAPI.SpVoice":
            voice = _FakeVoice(russian_voices,
                               setvoice_raises=setvoice_raises)
            state["voice"] = voice
            return voice
        if progid == "SAPI.SpAudioFormat":
            fmt = _FakeAudioFormat()
            state["formats"].append(fmt)
            return fmt
        if progid == "SAPI.SpFileStream":
            if stream_open_raises:
                class _Boom(_FakeFileStream):
                    def Open(self, path, mode):
                        raise OSError("file stream unavailable")
                stream = _Boom(payload)
            else:
                stream = _FakeFileStream(payload)
            state["streams"].append(stream)
            return stream
        raise NativeSapiError(f"unknown progid {progid}")

    client.CreateObject = _create
    com.client = client
    monkeypatch.setitem(sys.modules, "comtypes", com)
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    return state


def test_native_renderer_binds_com_and_renders_in_process(monkeypatch):
    state = _install_fake_comtypes(monkeypatch)
    renderer = NativeSapiRenderer(rate=2, volume=90)
    wav = renderer.render_wav("Здравствуйте")

    voice = state["voice"]
    stream = state["streams"][0]
    assert state["coinit"] >= 1
    assert voice.Rate == 2 and voice.Volume == 90
    assert voice.setoutput_calls == []  # SetOutput is absent from IDispatch
    assert voice.AudioOutputStream is stream  # property putref binds output
    assert len(voice.setvoice_calls) == 1  # Russian token via SetVoice method
    assert voice.Voice is None              # no ambiguous property put
    # synchronous default flag: purge-before-speak produces empty WAVs
    assert voice.calls == [("Здравствуйте", sapi_com.SVSF_DEFAULT)]
    assert sapi_com.SVSF_DEFAULT == 0
    # explicit compatible audio format is pinned before the file opens
    assert state["formats"][0].Type == sapi_com.SAFT_22KHZ_16BIT_MONO
    assert isinstance(stream.Format, _FakeAudioFormat)
    assert stream.opened[1] == sapi_com.SSFM_CREATE_FOR_WRITE
    assert stream.order[0] == "open"
    assert stream.closed >= 1
    assert not Path(stream.opened[0]).exists()  # temp WAV removed
    assert wav[:4] == b"RIFF"

    # second phrase reuses the bound voice (no rebind) but takes a fresh stream
    coinit_before = state["coinit"]
    renderer.render_wav("Ещё фраза")
    assert state["coinit"] == coinit_before
    assert len(voice.calls) == 2
    assert len(state["streams"]) == 2


def test_native_renderer_without_russian_voice_keeps_default(monkeypatch):
    state = _install_fake_comtypes(monkeypatch, russian_voices=0)
    renderer = NativeSapiRenderer()
    renderer.render_wav("Текст")
    voice = state["voice"]
    assert voice.Voice is None and voice.setvoice_calls == []
    assert voice.calls  # synthesis still happened


def test_native_renderer_falls_back_to_property_put(monkeypatch):
    state = _install_fake_comtypes(monkeypatch, setvoice_raises=True)
    renderer = NativeSapiRenderer()
    renderer.render_wav("Метод не поддержан")
    voice = state["voice"]
    assert len(voice.setvoice_calls) == 0
    assert voice.Voice is not None  # property assignment fallback worked
    assert voice.calls


def test_native_renderer_com_failure_raises(monkeypatch):
    _install_fake_comtypes(monkeypatch, create_error=OSError("no registry"))
    with pytest.raises(NativeSapiError):
        NativeSapiRenderer().render_wav("Сбой")


class _RawComPointer:
    def __init__(self):
        self.invoked = []

    def GetIDsOfNames(self, name):
        return [{"SetOutput": 21, "SetVoice": 19}[name]]

    def _invoke(self, dispid, invkind, named, *args):
        self.invoked.append((dispid, invkind, args))


class _TypeCompDispatch:
    """Mimics comtypes lazybind: ITypeComp cannot bind a real SAPI method."""

    def __init__(self):
        self._comobj = _RawComPointer()
        self.AudioOutputStream = None
        self.Voice = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise NameError(f"Name {name} not found")


def test_call_falls_back_to_dispid_invoke():
    from app.voice.sapi_com import DISPATCH_METHOD, _call

    assert DISPATCH_METHOD == 1  # wtypes.h stable value
    obj = _TypeCompDispatch()
    _call(obj, "SetOutput", "stream-x", True)
    assert obj._comobj.invoked == [(21, DISPATCH_METHOD, ("stream-x", True))]
    _call(obj, "SetVoice", "token-y")
    assert obj._comobj.invoked[-1] == (19, DISPATCH_METHOD, ("token-y",))


def test_native_renderer_empty_wav_raises_and_cleans_temp(monkeypatch):
    state = _install_fake_comtypes(monkeypatch, payload=b"")
    with pytest.raises(NativeSapiError):
        NativeSapiRenderer().render_wav("Тихо")
    assert not Path(state["streams"][0].opened[0]).exists()


def test_native_renderer_header_only_wav_raises(monkeypatch):
    header_only = io.BytesIO()
    with wave.open(header_only, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)  # zero frames -> bare 44-byte header
    state = _install_fake_comtypes(monkeypatch, payload=header_only.getvalue())
    assert 0 < len(header_only.getvalue()) <= 46
    with pytest.raises(NativeSapiError, match="header-only"):
        NativeSapiRenderer().render_wav("Нет фреймов")
    assert not Path(state["streams"][0].opened[0]).exists()


def test_native_renderer_stream_open_failure_cleans_temp(monkeypatch):
    state = _install_fake_comtypes(monkeypatch, stream_open_raises=True)
    with pytest.raises(NativeSapiError):
        NativeSapiRenderer().render_wav("Сбой потока")
    # mkstemp created the path before Open; finally block must remove it
    leftover = list(Path(tempfile.gettempdir()).glob("sapi_*.wav"))
    assert not leftover


def test_decode_wav_rejects_non_16bit():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)  # 8-bit — SAPI never emits this
        wf.setframerate(8000)
        wf.writeframes(b"\x80" * 800)
    with pytest.raises(NativeSapiError):
        decode_wav(buf.getvalue())


def test_sapitts_uses_native_path_without_subprocess(monkeypatch):
    _install_fake_comtypes(monkeypatch)

    def _boom(*args, **kwargs):
        raise AssertionError("PowerShell must not be spawned on native path")

    monkeypatch.setattr("app.voice.tts.subprocess.run", _boom)
    tts = SapiTts(TtsConfig(provider="sapi", sample_rate=24000))
    pcm = tts._synthesize_blocking("Прямой COM-рендер")
    # 0.25 s of 22.05 kHz mono resampled to 24 kHz, 16-bit
    assert len(pcm) == pytest.approx(0.25 * 24000 * 2, rel=0.05)
    assert tts._native_disabled is False


def test_sapitts_falls_back_to_powershell_when_com_dead(monkeypatch):
    _install_fake_comtypes(monkeypatch, create_error=OSError("com dead"))

    def fake_run(command, **kwargs):
        encoded = command[command.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        out_b64 = [ln for ln in script.splitlines() if "$out = " in ln][0] \
            .split("FromBase64String('")[1].split("')")[0]
        out_path = base64.b64decode(out_b64).decode("utf-8")
        with open(out_path, "wb") as fh:
            fh.write(_wav_bytes(sample_rate=16000))

    monkeypatch.setattr("app.voice.tts.subprocess.run", fake_run)
    tts = SapiTts(TtsConfig(provider="sapi", sample_rate=24000))
    pcm = tts._synthesize_blocking("Аварийный путь")
    assert pcm
    assert tts._native_disabled is True

    # native path stays disabled: second phrase goes straight to PowerShell
    calls = {"n": 0}

    def counting_run(command, **kwargs):
        calls["n"] += 1
        fake_run(command, **kwargs)

    monkeypatch.setattr("app.voice.tts.subprocess.run", counting_run)
    tts._synthesize_blocking("Вторая фраза")
    assert calls["n"] == 1
