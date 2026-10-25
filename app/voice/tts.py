"""Text-to-speech providers.

* YandexTtsV3 — branded Russian voices, gRPC streaming, sub-300 ms first chunk;
* F5Tts        — local SOTA zero-shot voice clone (https://github.com/SWivid/F5-TTS);
* MockTts      — offline PCM generator keeping the pipeline fully runnable.
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct

from ..config import TtsConfig

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


def build_tts(config: TtsConfig) -> TTSProvider:
    if config.provider == "yandex_v3":
        return YandexTtsV3(config)
    if config.provider == "f5":
        return F5Tts(config)
    log.info("TTS running in mock mode")
    return MockTts(config)
