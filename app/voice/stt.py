"""Speech-to-text providers behind one async interface.

* YandexSpeechKitV3 — streaming gRPC, best-in-class Russian with punctuation;
* FasterWhisperSTT — fully local CTranslate2 backend (CUDA/CPU);
* MockSTT          — deterministic offline provider for tests and demo mode.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import SttConfig

log = logging.getLogger(__name__)


@dataclass
class SttEvent:
    kind: str          # "interim" | "final"
    text: str
    confidence: float = 1.0


class STTProvider:
    """Streaming contract. ``events`` yields interim/final recognized phrases."""

    sample_rate = 16000

    async def start(self) -> None: ...
    async def push_pcm(self, pcm: bytes) -> None: ...
    async def flush(self) -> None:
        """Force end-of-utterance (e.g. on call hangup)."""
    async def stop(self) -> None: ...
    def events(self) -> asyncio.Queue[SttEvent]: ...


class MockSTT(STTProvider):
    """Replays scripted phrases; used by tests and by the no-API demo build."""

    def __init__(self, phrases: list[str] | None = None):
        self._queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        self._phrases = list(phrases or [])
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def push_pcm(self, pcm: bytes) -> None:
        # 64 KB of speech (≈2 s) consumes one scripted phrase
        if self._phrases and len(pcm) >= 32000:
            await self._queue.put(SttEvent("final", self._phrases.pop(0)))

    async def flush(self) -> None:
        if self._phrases:
            await self._queue.put(SttEvent("final", self._phrases.pop(0)))

    async def stop(self) -> None:
        self._started = False

    def events(self) -> asyncio.Queue[SttEvent]:
        return self._queue


class YandexSpeechKitV3(STTProvider):
    """Yandex SpeechKit streaming recognition v3 over gRPC.

    Requires ``pip install grpcio`` and the generated stubs
    (``yandex/cloud/ai/stt/v3/stt_service_pb2.py`` ...), shipped via the
    ``yandexcloud`` package or generated from the public proto bundle.
    """

    def __init__(self, config: SttConfig):
        self.cfg = config
        self._queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        self._stub = None
        self._call = None
        self._reader: asyncio.Task | None = None
        self._channel = None

    async def start(self) -> None:
        from grpc import aio  # noqa: PLC0415
        from yandex.cloud.ai.stt.v3 import stt_service_pb2_grpc, stt_service_pb2  # noqa: PLC0415

        creds = aio.ssl_channel_credentials()
        self._channel = aio.secure_channel("stt.api.cloud.yandex.net:443", creds)
        self._stub = stt_service_pb2_grpc.RecognizerStub(self._channel)

        metadata = (("authorization", f"Api-Key {self.cfg.api_key}"),)
        self._call = self._stub.RecognizeStreaming(metadata=metadata)

        opts = stt_service_pb2.StreamingOptions(
            recognition_model=stt_service_pb2.RecognitionModelOptions(
                audio_format=stt_service_pb2.AudioFormatOptions(
                    raw_audio=stt_service_pb2.RawAudioOptions(
                        sample_rate=self.sample_rate,
                        audio_encoding=stt_service_pb2.RawAudioOptions.LINEAR16_PCM,
                    )
                ),
                language_restriction=stt_service_pb2.LanguageRestrictionOptions(
                    restriction_type=stt_service_pb2.LanguageRestrictionOptions.WHITELIST,
                    language_code=[self.cfg.language],
                ),
                audio_processing_type=stt_service_pb2.RecognitionModelOptions.REAL_TIME,
            ),
        )
        await self._call.write(stt_service_pb2.StreamingRequest(session_options=opts))
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for response in self._call:
                for chunk in getattr(response, "final", None) and response.final.alternatives or []:
                    text = chunk.text.strip()
                    if text:
                        await self._queue.put(SttEvent("final", text, getattr(chunk, "confidence", 1.0)))
                for chunk in getattr(response, "partial", None) and response.partial.alternatives or []:
                    text = chunk.text.strip()
                    if text:
                        await self._queue.put(SttEvent("interim", text))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Yandex STT stream terminated")

    async def push_pcm(self, pcm: bytes) -> None:
        from yandex.cloud.ai.stt.v3 import stt_service_pb2  # noqa: PLC0415
        if self._call is None:
            raise RuntimeError("STT stream not started")
        await self._call.write(
            stt_service_pb2.StreamingRequest(audio_chunk=stt_service_pb2.AudioChunk(data=pcm))
        )

    async def flush(self) -> None:
        if self._call is not None:
            await self._call.done_writing()

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._call is not None:
            await self._call.done_writing()
        if self._channel:
            await self._channel.close()

    def events(self) -> asyncio.Queue[SttEvent]:
        return self._queue


class FasterWhisperSTT(STTProvider):
    """Local streaming-emulation via VAD-segmented chunk transcription."""

    def __init__(self, config: SttConfig):
        self.cfg = config
        self._queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # noqa: PLC0415
            self._model = WhisperModel(self.cfg.model, device=self.cfg.device, compute_type="int8")
        return self._model

    async def start(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._ensure_model)

    async def push_pcm(self, pcm: bytes) -> None:
        # Chunk-level transcription is driven by the engine on utterance end;
        # this provider simply buffers here.
        self._buffer = getattr(self, "_buffer", b"") + pcm

    async def flush(self) -> None:
        pcm = getattr(self, "_buffer", b"")
        if not pcm:
            return
        self._buffer = b""
        loop = asyncio.get_running_loop()
        text, info = await loop.run_in_executor(None, self._transcribe, pcm)
        if text:
            await self._queue.put(SttEvent("final", text, info.language_probability))

    def _transcribe(self, pcm: bytes):
        import numpy as np  # noqa: PLC0415
        audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
        segments, info = self._ensure_model().transcribe(audio, language="ru", vad_filter=True)
        return " ".join(s.text for s in segments).strip(), info

    async def stop(self) -> None:
        await self.flush()

    def events(self) -> asyncio.Queue[SttEvent]:
        return self._queue


def build_stt(config: SttConfig, mock_phrases: list[str] | None = None) -> STTProvider:
    if config.provider == "yandex_v3":
        return YandexSpeechKitV3(config)
    if config.provider == "faster_whisper":
        return FasterWhisperSTT(config)
    log.info("STT running in mock mode")
    return MockSTT(mock_phrases)
