"""Interactive text call simulator.

Drives the exact production brain (``CallEngine`` + ``DialogManager``) from
the keyboard instead of a microphone: every typed phrase is handled exactly
like an STT final result, replies are optionally spoken through the default
audio output, and the finished call lands in the same SQLite registry as a
SIP call, tagged ``source='text'``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

from ..config import AppSettings
from ..voice.tts import TTSProvider, build_tts

log = logging.getLogger(__name__)

VIRTUAL_PHONE = "+7 (999) 000-TEST"

try:
    import sounddevice as sd  # type: ignore
    AUDIO_AVAILABLE = True
except Exception as exc:  # OSError without PortAudio, ImportError without wheel
    sd = None  # type: ignore
    AUDIO_AVAILABLE = False
    _AUDIO_ERROR = exc


class SpeakerPlayer:
    """Plays 16-bit PCM on the default output; safe no-op without PortAudio."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return AUDIO_AVAILABLE

    def play(self, pcm: bytes, sample_rate: int) -> None:
        if not AUDIO_AVAILABLE or not pcm:
            return
        self.stop()
        thread = threading.Thread(target=self._run, args=(pcm, sample_rate), daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

    def _run(self, pcm: bytes, sample_rate: int) -> None:
        try:
            import numpy as np
            data = np.frombuffer(pcm, dtype="<i2")
            sd.play(data, samplerate=sample_rate)
            sd.wait()
        except Exception:
            log.exception("text simulator playback failed")

    def stop(self) -> None:
        if not AUDIO_AVAILABLE:
            return
        try:
            sd.stop()
        except Exception:
            pass


@dataclass
class SimulatorEvent:
    reply: str
    state: str
    slots: dict
    missing: list
    tool_names: list
    rtt_ms: int
    product: dict | None
    order: dict | None
    at: float
    spoken: bool


class TextSimulatorAgent:
    """Virtual inbound caller driven by typed phrases."""

    def __init__(self, engine, settings: AppSettings,
                 tts: TTSProvider | None = None, player: SpeakerPlayer | None = None):
        self.engine = engine
        self.settings = settings
        self.tts = tts or build_tts(settings.tts)
        self.player = player or SpeakerPlayer()
        self.muted = not self.player.available  # auto-mute when no audio device
        self.call_id: int | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def audio_available(self) -> bool:
        return self.player.available

    async def start(self) -> dict:
        """Emulate the inbound call and the agent's greeting."""
        info = await self.engine.start_text_call(VIRTUAL_PHONE)
        self.call_id = info["call_id"]
        self._active = True
        await self._speak(info["greeting"])
        return info

    async def send_user_utterance(self, text: str) -> SimulatorEvent:
        if not self._active:
            raise RuntimeError("simulator session is not active")
        text = text.strip()
        if not text:
            raise ValueError("utterance is empty")
        data = await self.engine.send_text_utterance(text)
        spoken = await self._speak(data["reply"])
        return SimulatorEvent(
            reply=data["reply"], state=data["state"], slots=data["slots"],
            missing=data["missing"], tool_names=data["tool_names"],
            rtt_ms=data["rtt_ms"], product=data["product"], order=data["order"],
            at=data["at"], spoken=spoken,
        )

    async def hangup(self) -> dict:
        """Finalize status, render the PDF quote when an order exists, close."""
        if not self._active:
            raise RuntimeError("simulator session is not active")
        self.player.stop()
        result = await self.engine.end_text_call()
        self._active = False
        return result

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.player.stop()

    async def _speak(self, text: str) -> bool:
        if self.muted or not text or not self.player.available:
            return False
        try:
            pcm = await self.tts.synthesize(text)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.player.play, pcm, self.tts.sample_rate)
            return True
        except Exception:
            log.exception("TTS playback failed, continuing muted")
            return False
