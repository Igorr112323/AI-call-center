"""Text-to-speech providers.

* ChainedTts  — ordered failover chain, dead providers are skipped once;
* PiperTts    — local neural VITS voice (ru_RU Irina), ONNX, fully offline;
* SapiTts     — built-in offline Windows speech (System.Speech), no keys;
* YandexTtsV3 — branded Russian voices, gRPC streaming, sub-300 ms first chunk;
* F5Tts       — local SOTA zero-shot voice clone (https://github.com/SWivid/F5-TTS);
* MockTts     — offline PCM tone keeping the pipeline fully runnable.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import TtsConfig
from .edge_tts_provider import EdgeTts, EdgeUnavailable
from .piper_tts import PiperTts, is_piper_available
from .sapi_com import decode_wav as decode_wav_bytes

log = logging.getLogger(__name__)


class TTSProvider:
    sample_rate = 24000

    async def synthesize(self, text: str) -> bytes:
        """Return full 16-bit mono PCM for ``text``."""
        raise NotImplementedError

    async def stream_synthesize(self, text: str):
        """Default implementation: one chunk. Real providers override per sentence."""
        yield await self.synthesize(text)

    async def aclose(self) -> None: ...


class ChainedTts(TTSProvider):
    """Try providers in order; a provider that fails once is retired.

    Guarantees the call never crashes on speech errors: when every real
    provider is dead the final MockTts produces the timing tone, and a
    total failure still degrades to short PCM silence.
    """

    def __init__(self, providers: list, sample_rate: int):
        self.providers = [p for p in providers if p is not None]
        self.sample_rate = sample_rate
        self._retired: set[int] = set()

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        for provider in self.providers:
            if id(provider) in self._retired:
                continue
            try:
                return await provider.synthesize(text)
            except Exception:  # noqa: BLE001 - speech must never break a call
                self._retired.add(id(provider))
                log.warning("TTS provider %s failed, failing over",
                            type(provider).__name__, exc_info=True)
        log.error("All TTS providers failed, returning silence")
        return b"\x00\x00" * (self.sample_rate // 4)

    async def aclose(self) -> None:
        for provider in self.providers:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001
                pass


class MockTts(TTSProvider):
    """Generates a soft 220 Hz tone proportional to phrase length.

    Keeps timing/barge-in logic testable without any model or network.
    """

    def __init__(self, config: TtsConfig):
        self.cfg = config
        self.sample_rate = config.sample_rate

    async def synthesize(self, text: str) -> bytes:
        words = max(1, len(text.split()))
        duration_sec = min(6.0, 0.35 * words / max(0.5, self.cfg.speed))
        n = int(self.sample_rate * duration_sec)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _tone_pcm, n, self.sample_rate)


class SapiTts(TTSProvider):
    """Offline Windows voice via the built-in System.Speech (SAPI) engine.

    Primary path is an in-process COM renderer (no process spawn per phrase,
    sub-100 ms overhead). The PowerShell subprocess renderer is kept as an
    automatic fallback when the COM stack is unavailable, and total failure
    degrades to short silence so a live call never crashes.
    """

    def __init__(self, config: TtsConfig):
        self.cfg = config
        self.sample_rate = config.sample_rate
        self._native = None          # type: ignore[assignment]
        self._native_disabled = False

    def _rate(self) -> int:
        return max(-10, min(10, round((self.cfg.speed - 1.0) * 4)))

    def _finish(self, pcm: bytes, src_rate: int, channels: int) -> bytes:
        from ..core.audio import resample_linear, to_mono

        return resample_linear(to_mono(pcm, channels), src_rate, self.sample_rate)

    def _render_native(self, text: str) -> bytes:
        """In-process SAPI over comtypes; raises when the stack is missing."""
        if self._native_disabled:
            raise RuntimeError("native SAPI previously failed")
        if self._native is None:
            from .sapi_com import NativeSapiRenderer  # noqa: PLC0415

            self._native = NativeSapiRenderer(rate=self._rate())
        wav = self._native.render_wav(text)
        pcm, src_rate, channels = decode_wav_bytes(wav)
        return self._finish(pcm, src_rate, channels)

    def _render_powershell(self, text: str) -> bytes:
        """Fallback: System.Speech through a PowerShell child process."""
        from ..core.audio import read_wav

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            wav_path = fh.name
        try:
            encoded = self._powershell_command(text, wav_path, self._rate())
            command = (
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
                if sys.platform == "win32"
                else ["pwsh", "-NoProfile", "-NonInteractive",
                      "-EncodedCommand", encoded]
            )
            subprocess.run(command, check=True, capture_output=True, timeout=30)
            pcm, src_rate, channels = read_wav(Path(wav_path))
            return self._finish(pcm, src_rate, channels)
        finally:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except OSError:
                pass

    def _synthesize_blocking(self, text: str) -> bytes:
        try:
            return self._render_native(text)
        except Exception:  # noqa: BLE001 - first failure disables native path
            log.warning("in-process SAPI unavailable, trying PowerShell fallback",
                        exc_info=True)
            self._native_disabled = True
        try:
            return self._render_powershell(text)
        except Exception:  # noqa: BLE001 - speech must never break a live call
            log.exception("SAPI synthesis failed; returning silence")
            return b"\x00\x00" * (self.sample_rate // 4)  # 0.25 s silence

    @staticmethod
    def _powershell_command(text: str, wav_path: str, rate: int) -> str:
        """Build the PS script; text/path ride as base64 to dodge quoting."""
        text_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        path_b64 = base64.b64encode(wav_path.encode("utf-8")).decode("ascii")
        script = f"""
Add-Type -AssemblyName System.Speech
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{text_b64}'))
$out = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path_b64}'))
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
  $ru = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -like 'ru*' }}
  if ($ru) {{ $s.SelectVoice($ru[0].VoiceInfo.Name) }}
}} catch {{}}
$s.Rate = {rate}
$s.SetOutputToWaveFile($out)
$s.Speak($text)
$s.Dispose()
"""
        # -EncodedCommand expects UTF-16LE base64
        return base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._synthesize_blocking, text)


class YandexTtsV3(TTSProvider):
    """SpeechKit v3 utterance synthesis (gRPC, streaming audio chunks)."""

    def __init__(self, config: TtsConfig):
        self.cfg = config
        self.sample_rate = config.sample_rate
        self._channel = None

    async def stream_synthesize(self, text: str):
        from grpc import aio  # noqa: PLC0415
        from yandex.cloud.ai.tts.v3 import tts_service_pb2, tts_service_pb2_grpc  # noqa: PLC0415

        if self._channel is None:
            creds = aio.ssl_channel_credentials()
            self._channel = aio.secure_channel("tts.api.cloud.yandex.net:443", creds)
        stub = tts_service_pb2_grpc.SynthesizerStub(self._channel)
        metadata = (("authorization", f"Api-Key {self.cfg.api_key}"),)

        request = tts_service_pb2.SynthesizerUtterance(
            utterance=tts_service_pb2.Utterance(text=tts_service_pb2.TextTurn(text=text)),
            audio_spec=tts_service_pb2.AudioFormatOptions(
                raw_audio=tts_service_pb2.RawAudio(
                    sample_rate=self.sample_rate,
                    audio_encoding=tts_service_pb2.RawAudio.LINEAR16_PCM,
                )
            ),
            hints=[
                tts_service_pb2.Hints(voice=self.cfg.voice),
                tts_service_pb2.Hints(speed=self.cfg.speed),
            ],
        )
        async for response in stub.UtteranceSynthesis(request, metadata=metadata):
            if response.audio_chunk.data:
                yield response.audio_chunk.data

    async def synthesize(self, text: str) -> bytes:
        chunks = [chunk async for chunk in self.stream_synthesize(text)]
        return b"".join(chunks)

    async def aclose(self) -> None:
        if self._channel:
            await self._channel.close()


class F5Tts(TTSProvider):
    """Local F5-TTS zero-shot clone. Reference voice is a 10-15 s manager WAV."""

    def __init__(self, config: TtsConfig, ref_wav_path: str = "", ref_text: str = ""):
        self.cfg = config
        self.sample_rate = 24000
        self.ref_wav_path = ref_wav_path
        self.ref_text = ref_text
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from f5_tts.api import F5TTS as _F5  # noqa: PLC0415
            self._model = _F5()
        return self._model

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._infer, text)

    def _infer(self, text: str) -> bytes:
        import soundfile as sf  # noqa: PLC0415
        model = self._ensure_model()
        wav, sr, _ = model.infer(
            ref_file=self.ref_wav_path, ref_text=self.ref_text,
            gen_text=text, speed=self.cfg.speed,
        )
        import io  # noqa: PLC0415
        buf = io.BytesIO()
        sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()


def _tone_pcm(n_samples: int, sample_rate: int, freq: float = 220.0) -> bytes:
    """Sine with 10 ms fade in/out to avoid clicks — pure stdlib struct packing."""
    frames = bytearray()
    fade = min(int(sample_rate * 0.01), n_samples // 2)
    for i in range(n_samples):
        gain = 1.0
        if i < fade:
            gain = i / fade
        elif i > n_samples - fade:
            gain = max(0.0, (n_samples - i) / fade)
        # quarter-scale amplitude: audible in tests, gentle on a phone line
        sample = int(0.25 * gain * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        frames += struct.pack("<h", sample)
    return bytes(frames)


def _native_chain(config: TtsConfig, *, prefer: str) -> TTSProvider:
    """Offline chain: neural Piper first (when bundled), then OS SAPI, tone."""
    chain = []
    if prefer in ("auto", "piper") and is_piper_available():
        chain.append(PiperTts(config))
    if prefer in ("auto", "sapi") and sys.platform == "win32":
        chain.append(SapiTts(config))
    chain.append(MockTts(config))
    if len(chain) == 1:
        return chain[0]
    return ChainedTts(chain, config.sample_rate)


def _full_chain(config: TtsConfig) -> TTSProvider:
    """Best-quality chain: online Edge neural -> offline Piper -> SAPI -> tone."""
    chain = [EdgeTts(config)]
    if is_piper_available():
        chain.append(PiperTts(config))
    if sys.platform == "win32":
        chain.append(SapiTts(config))
    chain.append(MockTts(config))
    return ChainedTts(chain, config.sample_rate)


def build_tts(config: TtsConfig) -> TTSProvider:
    if config.provider == "yandex_v3":
        return YandexTtsV3(config)
    if config.provider == "f5":
        return F5Tts(config)
    if config.provider == "mock":
        log.info("TTS running in mock mode")
        return MockTts(config)
    if config.provider == "edge":
        log.info("TTS running on Edge online neural voice")
        return _full_chain(config)
    if config.provider == "piper":
        if is_piper_available():
            log.info("TTS running on bundled Piper neural voice")
            return ChainedTts(
                [PiperTts(config),
                 SapiTts(config) if sys.platform == "win32" else None,
                 MockTts(config)], config.sample_rate)
        log.warning("Piper selected but model is not bundled; using native chain")
        return _native_chain(config, prefer="auto")
    if config.provider == "sapi":
        if sys.platform == "win32":
            log.info("TTS running on built-in Windows SAPI voice")
            return ChainedTts([SapiTts(config), MockTts(config)],
                              config.sample_rate)
        log.warning("SAPI TTS selected but host is %s; falling back to mock",
                    sys.platform)
        return MockTts(config)
    # "auto" (default): Edge neural -> Piper -> OS SAPI -> tone stub
    log.info("TTS auto chain: Edge neural, then offline fallbacks")
    return _full_chain(config)
