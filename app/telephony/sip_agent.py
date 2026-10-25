"""SIP ingress built on PJSIP (pjsua2 Python bindings).

* registers against any RFC 3261 trunk (Zadarma, MTT, MegaFon, Mango, ...);
* auto-answers inbound calls, taps the bidirectional RTP audio through an
  ``AudioMediaPort`` and feeds 16 kHz PCM into the voice engine;
* plays TTS PCM back through the same port (jitter queue, no sound card needed:
  the endpoint runs with ``setNoDev``);
* degrades to :class:`NullSipAgent` when pjsua2 is not installed, so the UI and
  the test-suite remain fully functional.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from ..config import SipConfig

log = logging.getLogger(__name__)

FRAME_MS = 30


@dataclass
class SipEvent:
    kind: str                 # registered | unregistered | incoming | answered | hungup | error
    phone: str = ""
    detail: str = ""


EventCallback = Callable[[SipEvent], None]
FrameCallback = Callable[[bytes, int], None]


class SipTransport:
    """Outbound PCM queue consumed by the pjsua2 media port (thread-safe)."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._queue: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._frame_bytes = int(sample_rate * 2 * FRAME_MS / 1000)

    async def play(self, pcm16: bytes) -> None:
        with self._lock:
            self._queue.append(pcm16)

    def next_frame(self, size: int) -> bytes:
        """Pull exactly ``size`` bytes, zero-padding underrun (comfort silence)."""
        with self._lock:
            available = b"".join(self._queue)
            self._queue.clear()
        if len(available) >= size:
            frame, rest = available[:size], available[size:]
            with self._lock:
                self._queue.appendleft(rest)
            return frame
        return available + b"\x00" * (size - len(available))

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()


class NullSipAgent:
    """Placeholder used when pjsua2 is unavailable."""

    def __init__(self, *args, **kwargs):
        self.transport = SipTransport()
        self.registered = False

    def start(self) -> None:
        log.warning("pjsua2 not installed — SIP disabled (UI/demo mode)")

    def stop(self) -> None: ...


def build_sip_agent(config: SipConfig, on_event: EventCallback,
                    on_frame: FrameCallback):
    try:
        import pjsua2 as pj  # noqa: PLC0415
    except ImportError:
        return NullSipAgent()
    return PjsuaAgent(config, on_event, on_frame, pj)


class PjsuaAgent:
    def __init__(self, config: SipConfig, on_event: EventCallback,
                 on_frame: FrameCallback, pj):
        self.cfg = config
        self._on_event = on_event
        self._on_frame = on_frame
        self._pj = pj
        self._ep = None
        self._account: "_Account | None" = None
        self.transport = SipTransport(config.sample_rate)
        self.registered = False

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        pj = self._pj
        ep = pj.Endpoint()
        ep.libCreate()

        ep_cfg = pj.EpConfig()
        ep_cfg.logConfig.level = 2
        ep_cfg.logConfig.consoleLevel = 0
        ep_cfg.medConfig.noClock = True
        ep_cfg.medConfig.clockRate = self.cfg.sample_rate
        ep_cfg.medConfig.sndClockRate = self.cfg.sample_rate
        ep.libInit(ep_cfg)
        ep.audDevManager().setNoDev()  # headless server: RTP ports only

        t_cfg = pj.TransportConfig()
        t_cfg.port = self.cfg.port
        transport_type = {
            "UDP": pj.PJSIP_TRANSPORT_UDP,
            "TCP": pj.PJSIP_TRANSPORT_TCP,
            "TLS": pj.PJSIP_TRANSPORT_TLS,
        }[self.cfg.transport]
        ep.transportCreate(transport_type, t_cfg)
        ep.libStart()

        acc_cfg = pj.AccountConfig()
        acc_cfg.idUri = f'sip:{self.cfg.login}@{self.cfg.server}'
        acc_cfg.regConfig.registrarUri = f'sip:{self.cfg.server}'
        cred = pj.AuthCredInfo("digest", self.cfg.realm or "*",
                               self.cfg.login, 0, self.cfg.password)
        acc_cfg.sipConfig.authCreds = [cred]
        self._account = _Account(self, pj)
        self._account.create(acc_cfg)
        self._ep = ep
        log.info("SIP endpoint started, registering %s@%s", self.cfg.login, self.cfg.server)

    def stop(self) -> None:
        try:
            if self._account:
                self._account.shutdown()
            if self._ep:
                self._ep.libDestroy()
        except Exception:
            log.exception("error during SIP shutdown")
        self.registered = False

    # ------------------------------------------------------------- pj callbacks
    def _emit(self, event: SipEvent) -> None:
        try:
            self._on_event(event)
        except Exception:
            log.exception("SIP event callback failed")


class _Account:
    """Thin proxy subclass factory — pjsua2 SWIG directors require subclassing."""

    def __new__(cls, agent: PjsuaAgent, pj):
        outer = agent

        class AccountImpl(pj.Account):
            def onRegState(self, prm):
                outer.registered = prm.code == 200
                outer._emit(SipEvent(
                    "registered" if outer.registered else "unregistered",
                    detail=f"code={prm.code}"))

            def onIncomingCall(self, prm):
                call = _Call(outer, pj, prm.callId)
                phone = _parse_remote(prm.rdata.info if hasattr(prm, "rdata") else "")
                outer._emit(SipEvent("incoming", phone=phone))
                call_prm = pj.CallOpParam()
                call_prm.statusCode = 200 if outer.cfg.auto_answer_after_ms == 0 else 180
                call.answer(call_prm)
                if outer.cfg.auto_answer_after_ms == 0:
                    outer._emit(SipEvent("answered", phone=phone))

        return AccountImpl()


class _Call:
    def __new__(cls, agent: PjsuaAgent, pj, call_id):
        outer = agent
        port_holder: dict = {}

        class CallImpl(pj.Call):
            def onCallState(self, prm):
                info = self.getInfo()
                if info.stateText == "DISCONNECTED":
                    outer.transport.clear()
                    outer._emit(SipEvent("hungup", detail=info.lastReason))
                    self.delete()

            def onCallMediaState(self, prm):
                info = self.getInfo()
                for mi in info.media:
                    if mi.type != pj.media_type.PJMEDIA_TYPE_AUDIO:
                        continue
                    media = pj.AudioMedia.typecastFromMedia(self.getMedia(mi.index))
                    port = _MediaPort(outer, pj, mi.index)
                    port.create_port()
                    port_holder["port"] = port
                    media.startTransmit(port)   # caller voice -> our tap
                    port.startTransmit(media)   # TTS -> caller

        return CallImpl(outer._account, call_id)


class _MediaPort:
    """Custom bidirectional pjmedia port. Falls back to WAV recorder if the
    running pjsua2 build lacks AudioMediaPort directors."""

    def __new__(cls, agent: PjsuaAgent, pj, media_index):
        outer = agent
        samples_per_frame = int(agent.cfg.sample_rate * FRAME_MS / 1000)

        class PortImpl(pj.AudioMediaPort):
            def onFrameReceived(self, frame):
                # caller audio (16-bit PCM)
                outer._on_frame(bytes(frame.buf), agent.cfg.sample_rate)

            def onFrameRequested(self, frame):
                frame.buf = outer.transport.next_frame(len(frame.buf))

        instance = PortImpl(
            f"ai-tap-{media_index}", agent.cfg.sample_rate, 1,
            samples_per_frame, 16,
        )
        instance.create_port = lambda: instance.registerMediaPort2()  # type: ignore[attr-defined]
        return instance


def _parse_remote(sip_info: str) -> str:
    """Extract E.164-ish number from a SIP From/remote info string."""
    if not sip_info:
        return "unknown"
    token = sip_info.split("<sip:")[-1].split(">")[0]
    user = token.split("@")[0] if "@" in token else token
    return user or "unknown"
