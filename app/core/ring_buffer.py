"""Thread-safe ring audio buffer.

Stores 16-bit PCM frames. The voice engine keeps a short pre-roll window so
that the first phonemes of an utterance are never clipped when VAD fires.
Byte-accurate tail trimming keeps exactly ``capacity_bytes`` of history.
"""
from __future__ import annotations

import threading


class AudioRingBuffer:
    def __init__(self, capacity_bytes: int):
        if capacity_bytes <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._buf.extend(data)
            overflow = len(self._buf) - self._capacity
            if overflow > 0:
                del self._buf[:overflow]

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def drain(self) -> bytes:
        with self._lock:
            data = bytes(self._buf)
            self._buf.clear()
            return data

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buf)

    @staticmethod
    def bytes_for_ms(duration_ms: int, sample_rate: int, channels: int = 1, bps: int = 16) -> int:
        return int(sample_rate * (bps // 8) * channels * duration_ms / 1000)
