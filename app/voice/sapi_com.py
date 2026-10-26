"""In-process SAPI5 speech rendering via comtypes (Windows only).

Unlike the PowerShell fallback this never spawns a process: the SAPI COM
objects live inside the application process, are initialised once per
worker thread, and each phrase is rendered into an in-memory WAV stream.
Late (dynamic) IDispatch binding is used on purpose so comtypes never needs
its on-disk generated-wrapper cache, which is unavailable in a frozen
PyInstaller onefile bundle.
"""
from __future__ import annotations

import io
import logging
import threading
import wave

log = logging.getLogger(__name__)

# ISpVoice::Speak flag: purge pending speech before speaking this phrase
SVSF_PURGE_BEFORE_SPEAK = 1
# SAPI voice-token attribute for ru-RU (LCID 0x0419)
RUSSIAN_VOICE_ATTRIBUTE = "Language=419"


class NativeSapiError(RuntimeError):
    """Raised when the in-process SAPI COM stack cannot be used."""


class NativeSapiRenderer:
    """Thread-local SAPI.SpVoice bound to an in-memory WAV stream."""

    def __init__(self, rate: int = 0, volume: int = 100):
        self.rate = max(-10, min(10, int(rate)))
        self.volume = max(0, min(100, int(volume)))
        self._local = threading.local()
        self._comtypes = None

    def _bind(self) -> None:
        """Lazy per-thread COM init; COM apartments are thread-affine."""
        try:
            import comtypes  # noqa: PLC0415
            import comtypes.client  # noqa: PLC0415
        except OSError as exc:  # import itself can fail on stripped Windows
            raise NativeSapiError(f"comtypes unavailable: {exc}") from exc
        self._comtypes = comtypes
        try:
            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001 - already initialised is fine
            pass
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
            stream = comtypes.client.CreateObject(
                "SAPI.SpMemoryStream", dynamic=True)
        except Exception as exc:  # noqa: BLE001
            raise NativeSapiError(f"SAPI COM objects unavailable: {exc}") from exc

        try:
            tokens = voice.GetVoices(RUSSIAN_VOICE_ATTRIBUTE)
            if tokens.Count:
                voice.Voice = tokens.Item(0)
                log.info("SAPI selected Russian voice: %s",
                         voice.Voice.GetDescription())
        except Exception:  # noqa: BLE001 - default voice still speaks
            log.warning("Russian SAPI voice not found, keeping system default")

        voice.Rate = self.rate
        voice.Volume = self.volume
        voice.AudioOutputStream = stream
        self._local.voice = voice
        self._local.stream = stream

    @staticmethod
    def _as_bytes(data) -> bytes:
        """GetData may come back as bytes or as a sequence of unsigned ints."""
        if isinstance(data, bytes):
            return data
        if isinstance(data, tuple) and data and isinstance(data[0], bytes):
            return data[0]
        return bytes(data)

    def render_wav(self, text: str) -> bytes:
        """Synthesize ``text`` synchronously, return complete WAV file bytes."""
        if not hasattr(self._local, "voice"):
            self._bind()
        self._local.voice.Speak(text, SVSF_PURGE_BEFORE_SPEAK)
        wav = self._as_bytes(self._local.stream.GetData())
        if not wav:
            raise NativeSapiError("SAPI returned an empty audio stream")
        return wav


def decode_wav(wav: bytes) -> tuple[bytes, int, int]:
    """Parse in-memory WAV bytes -> (16-bit PCM, sample rate, channels)."""
    with wave.open(io.BytesIO(wav), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise NativeSapiError(
                f"unexpected SAPI sample width: {wf.getsampwidth()} bytes")
        return wf.readframes(wf.getnframes()), wf.getframerate(), wf.getnchannels()
