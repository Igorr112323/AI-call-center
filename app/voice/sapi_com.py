"""In-process SAPI5 speech rendering via comtypes (Windows only).

Unlike the PowerShell fallback this never spawns a process: the SAPI COM
objects live inside the application process. A SpVoice is initialised once
per worker thread and every phrase is rendered through a lightweight
SpFileStream pointed at a temporary WAV file — COM object creation costs
microseconds, so per-phrase overhead stays well below the old PowerShell
process startup (~0.5 s). Late (dynamic) IDispatch binding is used on
purpose so comtypes never needs its on-disk generated-wrapper cache, which
is unavailable in a frozen PyInstaller onefile bundle.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import wave

log = logging.getLogger(__name__)

# ISpVoice::Speak synchronous default flag. SVSFPurgeBeforeSpeak (1) must be
# avoided: with an empty queue and a file/memory stream it purges the utterance
# itself, leaving a header-only WAV (verified empirically on SAPI 5.4).
SVSF_DEFAULT = 0
# SAPI voice-token attribute for ru-RU (LCID 0x0419)
RUSSIAN_VOICE_ATTRIBUTE = "Language=419"
# SpeechStreamFileMode::SSFMCreateForWrite
SSFM_CREATE_FOR_WRITE = 3
# IDispatch invocation kind (stable wtypes.h constants, avoids importing
# comtypes.automation on platforms where comtypes cannot load)
DISPATCH_METHOD = 1
# SpeechAudioFormatType::SAFT22kHz16BitMono — the classic SAPI WAV format
SAFT_22KHZ_16BIT_MONO = 22


class NativeSapiError(RuntimeError):
    """Raised when the in-process SAPI COM stack cannot be used."""


def _com_invoke(dispatch, name: str, *args):
    """Call an IDispatch method via GetIDsOfNames, bypassing ITypeComp.

    comtypes' lazybind resolves names through ITypeComp::Bind, which fails
    on some SAPI methods (e.g. SetOutput) even though plain IDispatch name
    lookup resolves them. Test doubles expose methods directly and never
    reach this path.
    """
    raw = dispatch._comobj
    dispid = raw.GetIDsOfNames(name)[0]
    return raw._invoke(dispid, DISPATCH_METHOD, 0, *args)


def _call(dispatch, name: str, *args):
    """Prefer direct attribute access; fall back to dispid-level Invoke."""
    try:
        attr = getattr(dispatch, name)
    except (AttributeError, NameError):
        return _com_invoke(dispatch, name, *args)
    return attr(*args)


class NativeSapiRenderer:
    """Thread-local SAPI.SpVoice; phrases land in temp WAV files."""

    def __init__(self, rate: int = 0, volume: int = 100):
        self.rate = max(-10, min(10, int(rate)))
        self.volume = max(0, min(100, int(volume)))
        self._local = threading.local()

    def _bind_voice(self):
        """Lazy per-thread COM init; COM apartments are thread-affine."""
        try:
            import comtypes  # noqa: PLC0415
            import comtypes.client  # noqa: PLC0415
        except OSError as exc:  # import itself can fail on stripped Windows
            raise NativeSapiError(f"comtypes unavailable: {exc}") from exc
        try:
            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001 - already initialised is fine
            pass
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
        except Exception as exc:  # noqa: BLE001
            raise NativeSapiError(f"SAPI SpVoice unavailable: {exc}") from exc

        self._select_russian_voice(voice)
        voice.Rate = self.rate
        voice.Volume = self.volume
        self._local.voice = voice
        self._local.comtypes = comtypes

    @staticmethod
    def _select_russian_voice(voice) -> None:
        """Pick an installed ru-RU voice without propput/propputref ambiguity.

        Dynamic dispatch sends PROPERTYPUTREF for object values while SAPI's
        Voice property is declared propput; the SetVoice method sidesteps
        this, the property assignment remains a second fallback.
        """
        try:
            tokens = voice.GetVoices(RUSSIAN_VOICE_ATTRIBUTE)
            if not tokens.Count:
                log.info("No Russian SAPI voice installed, using system default")
                return
            token = tokens.Item(0)
            try:
                _call(voice, "SetVoice", token)
            except Exception:  # noqa: BLE001
                voice.Voice = token
            log.info("SAPI selected Russian voice")
        except Exception:  # noqa: BLE001 - default voice still speaks
            log.warning("Russian SAPI voice selection failed, keeping default")

    def render_wav(self, text: str) -> bytes:
        """Synthesize ``text`` synchronously, return complete WAV file bytes."""
        if not hasattr(self._local, "voice"):
            self._bind_voice()
        comtypes = self._local.comtypes
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="sapi_")
        os.close(fd)
        stream = None
        wav = b""
        try:
            try:
                stream = comtypes.client.CreateObject(
                    "SAPI.SpFileStream", dynamic=True)
                # The automation AudioOutputStream putref is the equivalent
                # of SetOutput(stream, FALSE): the voice cannot renegotiate
                # the stream format. Pin an explicit compatible format up
                # front, otherwise Speak silently writes zero audio frames.
                audio_format = comtypes.client.CreateObject(
                    "SAPI.SpAudioFormat", dynamic=True)
                audio_format.Type = SAFT_22KHZ_16BIT_MONO
                stream.Format = audio_format
                stream.Open(wav_path, SSFM_CREATE_FOR_WRITE)
                # ISpeechVoice (the IDispatch interface) has no SetOutput
                # method — automation clients bind output through this
                # property; the explicit format above makes it non-negotiable
                self._local.voice.AudioOutputStream = stream
                self._local.voice.Speak(text, SVSF_DEFAULT)
                stream.Close()
                with open(wav_path, "rb") as fh:
                    wav = fh.read()
            except NativeSapiError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalise COM/ctypes errors
                raise NativeSapiError(f"SAPI render failed: {exc}") from exc
            # A 44-46 byte file is a bare WAV header: Speak wrote no frames
            if len(wav) <= 64:
                raise NativeSapiError(
                    f"SAPI produced a header-only WAV ({len(wav)} bytes)")
            return wav
        finally:
            if stream is not None:
                try:
                    stream.Close()
                except Exception:  # noqa: BLE001 - already closed is fine
                    pass
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def decode_wav(wav: bytes) -> tuple[bytes, int, int]:
    """Parse in-memory WAV bytes -> (16-bit PCM, sample rate, channels)."""
    with wave.open(io.BytesIO(wav), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise NativeSapiError(
                f"unexpected SAPI sample width: {wf.getsampwidth()} bytes")
        return wf.readframes(wf.getnframes()), wf.getframerate(), wf.getnchannels()
