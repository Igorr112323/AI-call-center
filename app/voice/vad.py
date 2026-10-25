"""Voice activity detection.

Primary: Silero VAD (https://github.com/snakers4/silero-vad) — the fastest
open-source Russian-tuned VAD. Fallback: deterministic energy VAD so the
pipeline and the test-suite work without a 50 MB torch dependency.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..config import VadConfig
from ..core.audio import rms_dbfs

log = logging.getLogger(__name__)

FRAME_MS = 30  # Silero operates on 30 ms frames at 16 kHz


@dataclass
class VadEvent:
    kind: str          # "start" | "end"
    at_ms: int


class UtteranceDetector:
    """Consumes fixed-size PCM frames, emits speech start/end with hangover.

    Implements barge-in semantics: ``speech_active`` flips immediately on the
    first voiced frame, while end-of-utterance waits ``min_silence_ms``.
    """

    def __init__(self, config: VadConfig, sample_rate: int = 16000):
        self.cfg = config
        self.sample_rate = sample_rate
        self.frame_ms = FRAME_MS
        self.speech_active = False
        self._silence_run_ms = 0
        self._speech_run_ms = 0
        self._impl = _build_vad(config)
        self._started_at = time.monotonic()

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._started_at) * 1000)

    def process(self, pcm: bytes) -> VadEvent | None:
        voiced = self._impl.is_speech(pcm, self.sample_rate)
        if voiced:
            self._silence_run_ms = 0
            self._speech_run_ms += self.frame_ms
            if not self.speech_active and self._speech_run_ms >= self.cfg.min_speech_ms:
                self.speech_active = True
                return VadEvent("start", self._now_ms())
        else:
            self._speech_run_ms = 0
            if self.speech_active:
                self._silence_run_ms += self.frame_ms
                if self._silence_run_ms >= self.cfg.min_silence_ms:
                    self.speech_active = False
                    self._silence_run_ms = 0
                    return VadEvent("end", self._now_ms())
        return None

    def reset(self) -> None:
        self.speech_active = False
        self._silence_run_ms = 0
        self._speech_run_ms = 0


class _EnergyVad:
    """Zero-dependency RMS gate. Used in tests and as offline fallback."""

    def __init__(self, threshold_db: float):
        self.threshold_db = threshold_db

    def is_speech(self, pcm: bytes, sample_rate: int) -> bool:
        return rms_dbfs(pcm) >= self.threshold_db


class _SileroVad:
    """Lazy Silero wrapper. torch/model load happens only when selected."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self._model = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: PLC0415 — heavy optional dependency
        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad",
            trust_repo=True, verbose=False,
        )
        self._model = model
        self._torch = torch

    def is_speech(self, pcm: bytes, sample_rate: int) -> bool:
        self._ensure_loaded()
        import array  # noqa: PLC0415
        samples = array.array("h")
        samples.frombytes(pcm)
        tensor = self._torch.tensor(samples, dtype=self._torch.float32) / 32768.0
        prob = float(self._model(tensor, sample_rate).item())
        return prob >= self.threshold


def _build_vad(config: VadConfig):
    if config.provider == "silero":
        try:
            return _SileroVad(config.threshold)
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("Silero VAD unavailable (%s); falling back to energy VAD", exc)
    return _EnergyVad(config.energy_threshold_db)
