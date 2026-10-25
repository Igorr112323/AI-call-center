import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import AppSettings


@pytest.fixture
def settings(tmp_path) -> AppSettings:
    return AppSettings.load(tmp_path / "settings.json")


def tone_pcm(duration_ms: int, sample_rate: int = 16000, freq: float = 300.0,
             amplitude: float = 0.6) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    return b"".join(
        struct.pack("<h", int(amplitude * 32767 * __import__("math").sin(
            2 * __import__("math").pi * freq * i / sample_rate)))
        for i in range(n)
    )


def silence_pcm(duration_ms: int, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(sample_rate * duration_ms / 1000)
