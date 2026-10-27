"""Cloud-neural TTS via Microsoft Edge online voices (no API key required).

Edge's public read-aloud service exposes the same natural neural voices as
Azure Speech (``ru-RU-SvetlanaNeural`` / ``ru-RU-DmitryNeural``). It needs
internet but no account or key, and the dependency is pure-Python. Output
arrives as 24 kHz mono MP3 which is decoded to the pipeline's 16-bit PCM
contract through the bundled libsndfile (soundfile).

Every failure raises EdgeUnavailable so ChainedTts can fail over to the
offline Piper/SAPI providers — speech errors must never break a live call.
"""
from __future__ import annotations

import asyncio
import io
import logging

from ..config import TtsConfig

log = logging.getLogger(__name__)

FEMALE_RU = "ru-RU-SvetlanaNeural"
MALE_RU = "ru-RU-DmitryNeural"


class EdgeUnavailable(RuntimeError):
    """Raised when the online neural voice cannot be reached/decoded."""


class EdgeTts:
    """TTSProvider-compatible online neural voice."""

    def __init__(self, config: TtsConfig):
        self.cfg = config
        self.sample_rate = config.sample_rate
        voice = (config.voice or "").strip()
        self.voice = voice if voice.startswith("ru-RU-") else FEMALE_RU

    def _rate(self) -> str:
        # speed 1.0 -> +0%, 2.0 -> +100%, 0.5 -> -50%, clamped to Edge bounds
        pct = max(-50, min(100, round((self.cfg.speed - 1.0) * 100)))
        return f"{'+' if pct >= 0 else ''}{pct}%"

    def _decode_mp3(self, mp3_bytes: bytes) -> bytes:
        """MP3 -> 16-bit mono PCM, resampled to the pipeline rate."""
        import soundfile as sf  # noqa: PLC0415

        from ..core.audio import resample_linear  # noqa: PLC0415

        data, src_rate = sf.read(io.BytesIO(mp3_bytes), dtype="int16",
                                 always_2d=False)
        if getattr(data, "ndim", 1) == 2:  # defensive: Edge is mono anyway
            data = data.mean(axis=1).astype("int16")
        return resample_linear(data.tobytes(), int(src_rate), self.sample_rate)

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        try:
            import edge_tts  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise EdgeUnavailable(f"edge-tts package unavailable: {exc}") from exc

        mp3 = bytearray()
        try:
            communicate = edge_tts.Communicate(
                text, self.voice, rate=self._rate())
            async for message in communicate.stream():
                if message.get("type") == "audio":
                    mp3.extend(message["data"])
        except EdgeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - network/DRM/websocket errors
            raise EdgeUnavailable(f"Edge speech service failed: {exc}") from exc

        if not mp3:
            raise EdgeUnavailable("Edge service returned no audio")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._decode_mp3, bytes(mp3))

    async def aclose(self) -> None:
        return None
