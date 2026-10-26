"""Offline neural TTS via Piper (https://github.com/rhasspy/piper, MIT).

Piper runs VITS models through ONNX Runtime on CPU — no torch, no network,
no API keys. The Russian female voice ``ru_RU-irina-medium`` is downloaded
at build time and bundled under ``app/resources/piper``. The heavy piper
package is imported lazily so the rest of the application (and the test
suite, which fakes the engine) works without the dependency installed.
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path

from ..config import TtsConfig
from ..paths import resource_path

log = logging.getLogger(__name__)

PIPER_DIR = "piper"
MODEL_STEM = "ru_RU-irina-medium"
MODEL_FILE = f"{MODEL_STEM}.onnx"
CONFIG_FILE = f"{MODEL_STEM}.onnx.json"


class PiperUnavailable(RuntimeError):
    """Raised when the Piper engine or its bundled model cannot be used."""


def piper_model_paths() -> tuple[Path, Path] | None:
    """Return (model, config) paths when both are bundled, else None."""
    model = resource_path(f"{PIPER_DIR}/{MODEL_FILE}")
    config = resource_path(f"{PIPER_DIR}/{CONFIG_FILE}")
    if model.exists() and config.exists():
        return model, config
    return None


def is_piper_available() -> bool:
    """Cheap pre-check without importing onnxruntime."""
    return piper_model_paths() is not None


class PiperTts:
    """Neural voice provider matching the TTSProvider PCM contract."""

    def __init__(self, config: TtsConfig):
        self.cfg = config
        self.sample_rate = config.sample_rate
        self._voice = None
        self._synth_config_cls = None
        self._load_failed = False

    def _length_scale(self) -> float:
        # Piper length_scale > 1 means slower speech; invert the speed knob.
        scale = 1.0 / max(0.5, self.cfg.speed)
        return max(0.6, min(1.8, scale))

    def _load_voice(self):
        if self._load_failed:
            raise PiperUnavailable("Piper failed to initialise earlier")
        paths = piper_model_paths()
        if paths is None:
            self._load_failed = True
            raise PiperUnavailable(
                f"bundled Piper model not found in {PIPER_DIR}/")
        model_path, config_path = paths
        try:
            from piper import PiperVoice, SynthesisConfig  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            self._load_failed = True
            raise PiperUnavailable(f"piper package unavailable: {exc}") from exc
        try:
            self._voice = PiperVoice.load(
                str(model_path), config_path=str(config_path))
            self._synth_config_cls = SynthesisConfig
            log.info("Piper neural voice loaded: %s", MODEL_STEM)
        except Exception as exc:  # noqa: BLE001
            self._load_failed = True
            raise PiperUnavailable(f"Piper model load failed: {exc}") from exc

    def _synthesize_blocking(self, text: str) -> bytes:
        if self._voice is None:
            self._load_voice()
        buf = io.BytesIO()
        # Released piper-tts API: synthesize_wav configures the wave writer
        # (rate/width/channels) itself and writes one chunk per sentence.
        syn_config = self._synth_config_cls(
            length_scale=self._length_scale())
        with wave.open(buf, "wb") as wav_writer:
            self._voice.synthesize_wav(
                text, wav_writer, syn_config=syn_config)
        buf.seek(0)
        with wave.open(buf, "rb") as wav_reader:
            pcm = wav_reader.readframes(wav_reader.getnframes())
            src_rate = wav_reader.getframerate()
            channels = wav_reader.getnchannels()
        from ..core.audio import resample_linear, to_mono  # noqa: PLC0415

        return resample_linear(to_mono(pcm, channels), src_rate, self.sample_rate)

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._synthesize_blocking, text)

    async def aclose(self) -> None:
        self._voice = None
