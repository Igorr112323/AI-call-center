"""Softphone test mode: laptop microphone + headphones, no SIP trunk involved.

Mirrors the SipTransport contract used by VoiceEngine:
  * microphone frames (16 kHz mono int16) are pushed into ``on_frame``;
  * ``play()`` enqueues TTS PCM for the output callback with zero underrun;
  * a throttled RMS callback feeds the UI VU-meter.

When PortAudio/sounddevice is unavailable the agent degrades to
:class:`NullLocalAgent` so the rest of the application stays fully usable.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Callable

from ..core.audio import rms_dbfs

log = logging.getLogger(__name__)

try:
    import sounddevice as sd  # type: ignore
    AVAILABLE = True
except Exception as exc:  # OSError on missing libportaudio, ImportError on wheel
    sd = None  # type: ignore
    AVAILABLE = False
    _IMPORT_ERROR = exc

FrameCallback = Callable[[bytes, int], None]
LevelCallback = Callable[[float], None]


class NullLocalAgent:
    available = False
    sample_rate = 16000

    def __init__(self, *args, **kwargs):
        self.transport = None

    def start(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "Локальный аудио-режим недоступен: не найден PortAudio/sounddevice"
        )

    def stop(self) -> None: ...

    async def play(self, pcm16: bytes) -> None: ...


class LocalAudioAgent:
    available = True
    sample_rate = 16000
    _frame_ms = 30

    def __init__(self, level_callback: LevelCallback | None = None):
        self._level_callback = level_callback
        self._play_queue: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._input = None
        self._output = None
        self._on_frame: FrameCallback | None = None
        self._last_level_emit = 0.0
        self._frame_bytes = int(self.sample_rate * 2 * self._frame_ms / 1000)

    # VoiceEngine transport contract
    async def play(self, pcm16: bytes) -> None:
        with self._lock:
            self._play_queue.append(pcm16)

    def start(self, on_frame: FrameCallback) -> None:
        if not audio_devices_available():
            raise RuntimeError("sounddevice/PortAudio недоступны или нет аудиоустройств")
        self._on_frame = on_frame
        self._input = sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16",
            blocksize=self._frame_bytes // 2, callback=self._input_callback,
        )
        self._output = sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16",
            blocksize=self._frame_bytes // 2, callback=self._output_callback,
        )
        self._input.start()
        self._output.start()
        log.info("local audio agent started (mic + playback @ %s Hz)", self.sample_rate)

    def stop(self) -> None:
        for stream in (self._input, self._output):
            try:
                if stream is not None:
                    stream.stop()
                    stream.close()
            except Exception:
                log.exception("error closing audio stream")
        self._input = self._output = None
        with self._lock:
            self._play_queue.clear()

    # ------------------------------------------------------------- PortAudio
    def _input_callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("input status: %s", status)
        pcm = bytes(indata.tobytes())
        if self._on_frame:
            try:
                self._on_frame(pcm, self.sample_rate)
            except Exception:
                log.exception("frame callback failed")
        if self._level_callback:
            now = time.monotonic()
            if now - self._last_level_emit >= 0.08:  # ~12 FPS for the VU meter
                self._last_level_emit = now
                try:
                    self._level_callback(rms_dbfs(pcm))
                except Exception:
                    pass

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            log.debug("output status: %s", status)
        needed = frames * 2
        with self._lock:
            available = b"".join(self._play_queue)
            self._play_queue.clear()
        if len(available) >= needed:
            chunk, rest = available[:needed], available[needed:]
            with self._lock:
                self._play_queue.appendleft(rest)
        else:
            chunk = available + b"\x00" * (needed - len(available))
        outdata[:] = self._to_numpy(chunk, frames)

    @staticmethod
    def _to_numpy(pcm: bytes, frames: int):
        import numpy as np
        return np.frombuffer(pcm[: frames * 2], dtype="<i2").reshape(-1, 1)


def audio_devices_available() -> bool:
    """True only when PortAudio is importable AND exposes at least one device.

    CI runners and headless servers often have the sounddevice wheel but no
    audio hardware: ``query_devices()`` then raises or returns an empty list.
    """
    if not AVAILABLE:
        return False
    try:
        return len(sd.query_devices()) > 0
    except Exception as exc:  # PortAudioError on device-less hosts
        log.warning("no audio devices visible to PortAudio: %s", exc)
        return False


def build_local_agent(level_callback: LevelCallback | None = None):
    if audio_devices_available():
        return LocalAudioAgent(level_callback)
    if AVAILABLE:
        log.warning("PortAudio present but no audio devices; local mic disabled")
    else:
        log.warning("sounddevice unavailable (%s); local mic mode disabled",
                    _IMPORT_ERROR if "_IMPORT_ERROR" in globals() else "unknown")
    return NullLocalAgent()
