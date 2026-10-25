"""Realtime voice pipeline: ring buffer -> VAD -> STT -> brain -> TTS, with barge-in.

Latency budget (target ≤ 600 ms end-to-end):
  VAD end-of-speech hangover ............ ~150 ms (configurable silence)
  STT finalization ...................... ~100-200 ms (streaming)
  LLM first token (Groq Llama-3.3-70B) .. ~200 ms
  TTS first chunk ....................... ~150 ms
The engine measures the actual figure on every turn and reports it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..config import TtsConfig, VadConfig
from ..core.audio import mix, resample_linear, write_wav
from ..core.ring_buffer import AudioRingBuffer
from .stt import STTProvider, SttEvent
from .tts import TTSProvider
from .vad import FRAME_MS, UtteranceDetector

log = logging.getLogger(__name__)

UserTextHandler = Callable[[str], Awaitable[str]]
EventHandler = Callable[[dict], Awaitable[None]] | Callable[[dict], None]


class AudioTransport(Protocol):
    """Telephony-side contract implemented by the SIP agent (and test doubles)."""

    sample_rate: int

    async def play(self, pcm16: bytes) -> None: ...


class VoiceEngine:
    def __init__(self, stt: STTProvider, tts: TTSProvider, vad_cfg: VadConfig,
                 tts_cfg: TtsConfig, transport: AudioTransport,
                 on_user_text: UserTextHandler,
                 on_event: EventHandler | None = None,
                 record_path: Path | None = None):
        self.stt = stt
        self.tts = tts
        self.vad_cfg = vad_cfg
        self.tts_cfg = tts_cfg
        self.transport = transport
        self.on_user_text = on_user_text
        self.on_event = on_event
        self.record_path = record_path

        self.sample_rate = 16000
        preroll = AudioRingBuffer.bytes_for_ms(vad_cfg.preroll_ms, self.sample_rate)
        self._ring = AudioRingBuffer(preroll)
        self._detector = UtteranceDetector(vad_cfg, self.sample_rate)
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._utterance = bytearray()
        self._bot_speaking = False
        self._cancel = asyncio.Event()
        self._playback_task: asyncio.Task | None = None
        self._pump_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._record_caller = bytearray()
        self._record_agent = bytearray()
        self._last_latency_ms = 0
        self._filler_index = 0
        self._final_buffer: deque = deque()

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.stt.start()
        self._pump_task = asyncio.create_task(self._pump())
        log.info("voice engine started (vad=%s, barge_in=%s)",
                 self.vad_cfg.provider, self.vad_cfg.barge_in)

    async def stop(self) -> Path | None:
        self._stopped.set()
        if self._playback_task:
            self._playback_task.cancel()
        if self._pump_task:
            self._pump_task.cancel()
        await self.stt.stop()
        await self._flush_recording()
        return self.record_path

    # -------------------------------------------------- thread-safe SIP entry
    def ingest_frame(self, pcm16: bytes, source_rate: int | None = None) -> None:
        """Called from the SIP media thread."""
        if source_rate and source_rate != self.sample_rate:
            pcm16 = resample_linear(pcm16, source_rate, self.sample_rate)
        try:
            self._incoming.put_nowait(pcm16)
        except asyncio.QueueFull:
            log.warning("incoming audio queue full, dropping oldest frame")
            self._incoming.get_nowait()
            self._incoming.put_nowait(pcm16)

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking

    @property
    def last_latency_ms(self) -> int:
        return self._last_latency_ms

    # ------------------------------------------------------------------ pump
    async def _pump(self) -> None:
        frame_bytes = AudioRingBuffer.bytes_for_ms(FRAME_MS, self.sample_rate)
        pending = bytearray()
        try:
            while not self._stopped.is_set():
                frame = await self._incoming.get()
                pending += frame
                self._record_caller += frame
                while len(pending) >= frame_bytes:
                    chunk = bytes(pending[:frame_bytes])
                    del pending[:frame_bytes]
                    await self._process_frame(chunk)
                # drain STT events without blocking audio intake
                await self._drain_stt_events(interim_only=True)
        except asyncio.CancelledError:
            pass

    async def _process_frame(self, frame: bytes) -> None:
        self._ring.write(frame)
        event = self._detector.process(frame)
        if event is None:
            return
        if event.kind == "start":
            self._utterance = bytearray(self._ring.snapshot())
            self._ring.clear()
            await self._emit({"type": "speech_started"})
            if self.vad_cfg.barge_in and self._bot_speaking:
                await self._handle_barge_in()
        elif event.kind == "end":
            self._utterance += frame
            await self._emit({"type": "speech_ended"})
            asyncio.create_task(self._finalize_utterance())

    async def _handle_barge_in(self) -> None:
        log.info("barge-in: interrupting TTS/LLM")
        self._cancel.set()
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
        await self._emit({"type": "barge_in"})

    async def _finalize_utterance(self) -> None:
        pcm = bytes(self._utterance)
        self._utterance.clear()
        if len(pcm) < AudioRingBuffer.bytes_for_ms(120, self.sample_rate):
            return
        t0 = time.monotonic()
        await self.stt.push_pcm(pcm)
        await self.stt.flush()
        text = await self._wait_final_text(timeout=4.0)
        if not text:
            return
        await self._emit({"type": "user_text", "text": text})
        reply = await self.on_user_text(text)
        self._last_latency_ms = max(1, int((time.monotonic() - t0) * 1000))
        await self._emit({"type": "latency", "ms": self._last_latency_ms})
        if reply:
            await self.speak(reply)

    async def _wait_final_text(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        queue = self.stt.events()
        while time.monotonic() < deadline:
            if self._final_buffer:
                event = self._final_buffer.popleft()
                if event.text.strip():
                    return event.text.strip()
                continue
            remaining = deadline - time.monotonic()
            try:
                event: SttEvent = await asyncio.wait_for(queue.get(), timeout=min(0.2, remaining))
            except asyncio.TimeoutError:
                continue
            if event.kind == "final" and event.text.strip():
                return event.text.strip()
        return ""

    async def _drain_stt_events(self, interim_only: bool) -> None:
        """Pump interim events to the UI without losing finals (barge-in race)."""
        queue = self.stt.events()
        while not queue.empty():
            event = queue.get_nowait()
            if event.kind == "interim":
                await self._emit({"type": "interim", "text": event.text})
            else:
                self._final_buffer.append(event)

    # -------------------------------------------------------------- speaking
    async def speak(self, text: str) -> None:
        if not text or self._stopped.is_set():
            return
        self._cancel.clear()
        self._bot_speaking = True
        task = asyncio.create_task(self._stream_tts(text))
        self._playback_task = task
        try:
            await task
        except asyncio.CancelledError:
            log.info("TTS playback cancelled")
        finally:
            self._bot_speaking = False

    async def _stream_tts(self, text: str) -> None:
        async for chunk in self.tts.stream_synthesize(text):
            if self._cancel.is_set():
                break
            if self.tts.sample_rate != self.sample_rate:
                chunk = resample_linear(chunk, self.tts.sample_rate, self.sample_rate)
            self._record_agent += chunk
            await self.transport.play(chunk)

    async def speak_filler(self) -> None:
        """Short canned phrase while a 1C/LLM tool call is in flight."""
        if self._bot_speaking:
            return
        filler = self.tts_cfg.fillers[self._filler_index % max(1, len(self.tts_cfg.fillers))]
        self._filler_index += 1
        await self.speak(filler)

    # ------------------------------------------------------------- recording
    async def _flush_recording(self) -> None:
        if not self.record_path or (not self._record_caller and not self._record_agent):
            return
        agent = bytes(self._record_agent)
        if self.tts.sample_rate != self.sample_rate:
            agent = resample_linear(agent, self.tts.sample_rate, self.sample_rate)
        caller = bytes(self._record_caller)
        length = min(len(caller), len(agent)) if agent else len(caller)
        if agent:
            mixed = mix(caller[:length], agent[:length]) + caller[length:] + agent[length:]
        else:
            mixed = caller
        write_wav(self.record_path, mixed, self.sample_rate, channels=1)
        log.info("recording saved: %s", self.record_path)

    async def _emit(self, payload: dict) -> None:
        if not self.on_event:
            return
        result = self.on_event(payload)
        if asyncio.iscoroutine(result):
            await result


class VirtualTransport:
    """In-memory transport for tests and headless demo mode."""

    sample_rate = 16000

    def __init__(self):
        self.played: list[bytes] = []
        self._lock = asyncio.Lock()

    async def play(self, pcm16: bytes) -> None:
        self.played.append(pcm16)

    @property
    def played_total_bytes(self) -> int:
        return sum(len(c) for c in self.played)
