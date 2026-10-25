"""PCM helpers: energy, WAV I/O, mixing — stdlib only (no numpy hard dependency)."""
from __future__ import annotations

import audioop
import math
import wave
from pathlib import Path

PCM_WIDTH = 2  # 16-bit signed little-endian, the telephony workhorse


def rms_dbfs(pcm: bytes) -> float:
    """RMS level in dBFS for 16-bit PCM. Silence returns -120."""
    if len(pcm) < PCM_WIDTH:
        return -120.0
    rms = audioop.rms(pcm, PCM_WIDTH)
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


def resample_linear(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Simple linear resample (mono, 16-bit). SIP legs are 8k, pipeline is 16k."""
    if src_rate == dst_rate:
        return pcm
    return audioop.ratecv(pcm, PCM_WIDTH, 1, src_rate, dst_rate, None)[0]


def to_mono(pcm: bytes, channels: int) -> bytes:
    if channels == 1:
        return pcm
    return audioop.tomono(pcm, PCM_WIDTH, 0.5, 0.5)


def mix(*streams: bytes) -> bytes:
    """Mix equally-sized 16-bit PCM streams (for two-party call recording)."""
    if not streams:
        return b""
    length = min(len(s) for s in streams)
    acc = streams[0][:length]
    for s in streams[1:]:
        acc = audioop.add(acc, s[:length], PCM_WIDTH)
    return acc


def write_wav(path: Path, pcm: bytes, sample_rate: int, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(PCM_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return path


def read_wav(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        return pcm, wf.getframerate(), wf.getnchannels()


def pcm_duration_ms(pcm: bytes, sample_rate: int, channels: int = 1) -> int:
    return int(len(pcm) / (PCM_WIDTH * channels * sample_rate) * 1000)


def format_timecode(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
