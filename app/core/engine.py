"""Application orchestration.

``CallEngine`` is a pure-asyncio service (testable without Qt); ``EngineThread``
runs it inside a QThread and re-emits domain events as Qt signals.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ..brain.dialog_manager import DialogManager, DialogState
from ..brain.llm import LlmClient
from ..brain.tools import ToolExecutor
from ..config import AppSettings
from ..integrations.one_c_client import OneCClient
from ..telephony.sip_agent import SipEvent, build_sip_agent
from ..voice.stt import build_stt
from ..voice.tts import build_tts
from ..voice.voice_engine import VirtualTransport, VoiceEngine
from .models import CallStatus, Speaker, TranscriptLine
from .storage import Storage

log = logging.getLogger(__name__)


class CallEngine:
    def __init__(self, settings: AppSettings, storage: Storage, data_dir: Path):
        self.settings = settings
        self.storage = storage
        self.data_dir = Path(data_dir)
        self.records_dir = self.data_dir / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

        self.one_c = OneCClient(settings.one_c)
        self.llm = LlmClient(settings.llm)
        self.sip = build_sip_agent(settings.sip, self._on_sip_event, self._on_sip_frame)
        self._stop = asyncio.Event()
        self._listeners: list = []
        self._voice: VoiceEngine | None = None
        self._dialog: DialogManager | None = None
        self._call_id: int | None = None
        self._phone = ""
        self._call_started_at = 0.0
        self._demo_transport: VirtualTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ events
    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def emit(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:
                log.exception("event listener failed")

    # ------------------------------------------------------------------ run/stop
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.settings.sip.enabled:
            await asyncio.get_running_loop().run_in_executor(None, self.sip.start)
        self.emit({"type": "engine_ready", "sip_registered": getattr(self.sip, "registered", False)})
        await self._stop.wait()

    async def shutdown(self) -> None:
        self._stop.set()
        if self._voice:
            await self._voice.stop()
        await asyncio.get_running_loop().run_in_executor(None, self.sip.stop)
        await self.one_c.aclose()
        await self.llm.aclose()
        self.storage.close()

    # --------------------------------------------------------------- SIP wiring
    def _on_sip_event(self, event: SipEvent) -> None:
        loop = self._loop or asyncio.get_event_loop()
        if event.kind == "incoming":
            asyncio.run_coroutine_threadsafe(self.start_call(event.phone), loop)
        elif event.kind == "hungup":
            asyncio.run_coroutine_threadsafe(self.end_call(), loop)
        else:
            self.emit({"type": "sip", "kind": event.kind, "detail": event.detail})

    def _on_sip_frame(self, pcm: bytes, sample_rate: int) -> None:
        if self._voice:
            self._voice.ingest_frame(pcm, sample_rate)

    # ------------------------------------------------------------- call lifecycle
    async def start_call(self, phone: str, mock_phrases: list[str] | None = None) -> int:
        if self._voice is not None:
            log.warning("call already active, rejecting start_call")
            return self._call_id or -1

        self._phone = phone
        self._call_started_at = time.monotonic()
        record_path = self.records_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.wav"
        self._call_id = self.storage.create_call(phone, str(record_path))
        self.emit({"type": "call_started", "call_id": self._call_id, "phone": phone})

        stt = build_stt(self.settings.stt, mock_phrases)
        tts = build_tts(self.settings.tts)
        transport = self.sip.transport if self.settings.sip.enabled else VirtualTransport()
        if isinstance(transport, VirtualTransport):
            self._demo_transport = transport

        executor = ToolExecutor(self.one_c, phone, on_tool_start=self._on_tool_start)
        self._dialog = DialogManager(self.llm, self.settings.scenario, executor)

        self._voice = VoiceEngine(
            stt, tts, self.settings.vad, self.settings.tts, transport,
            on_user_text=self._on_user_text, on_event=self._on_voice_event,
            record_path=record_path,
        )
        await self._voice.start()
        greeting = self._dialog.open_greeting()
        self._persist_line(Speaker.AGENT, greeting, 0.0)
        await self._voice.speak(greeting)
        return self._call_id

    async def end_call(self) -> None:
        if not self._voice:
            return
        duration = int(time.monotonic() - self._call_started_at)
        record_path = await self._voice.stop()
        self._voice = None

        if self._dialog:
            self._dialog.close()
        status = self._derive_status(duration)
        self.storage.update_call(
            self._call_id, duration_sec=duration, status=status,
            record_path=str(record_path) if record_path else None,
            summary=self._build_summary(),
        )
        self.emit({"type": "call_ended", "call_id": self._call_id,
                   "status": status.value, "duration": duration})
        self._dialog = None
        self._call_id = None

    def _derive_status(self, duration: int) -> CallStatus:
        if self._dialog and self._dialog.state is DialogState.ORDER_CREATED:
            return CallStatus.ORDER
        if duration < 5:
            return CallStatus.FAILED
        return CallStatus.CONSULTATION

    def _build_summary(self) -> str:
        if not self._dialog:
            return ""
        product = self._dialog.executor.last_product
        qty = self._dialog.slots.get("quantity")
        if product and product.found:
            return f"{product.name} ({qty or 1} шт)".strip()
        category = self._dialog.slots.get("category")
        return f"Запрос: {category}" if category else ""

    # ------------------------------------------------------- per-turn processing
    async def _on_user_text(self, text: str) -> str:
        assert self._dialog is not None
        elapsed = time.monotonic() - self._call_started_at
        self._persist_line(Speaker.CLIENT, text, elapsed)
        self.emit({"type": "transcript", "speaker": Speaker.CLIENT.value,
                   "text": text, "at": elapsed})

        result = await self._dialog.handle_utterance(text)
        if result.cancelled:
            return ""
        self._persist_line(Speaker.AGENT, result.reply_text, elapsed)
        self.emit({"type": "transcript", "speaker": Speaker.AGENT.value,
                   "text": result.reply_text, "at": elapsed,
                   "latency_ms": self._voice.last_latency_ms if self._voice else 0})

        if result.order and result.order.items:
            result.order.call_id = self._call_id
            order_id = self.storage.save_order(result.order)
            self.storage.update_call(self._call_id, status=CallStatus.ORDER)
            self.emit({"type": "order_saved", "call_id": self._call_id, "order_id": order_id,
                       "total": result.order.total,
                       "number_1c": result.order.number_1c})
        return result.reply_text

    async def _on_tool_start(self, tool_name: str) -> None:
        if tool_name in ("check_catalog_and_price", "create_order") and self._voice:
            await self._voice.speak_filler()

    def _on_voice_event(self, event: dict) -> None:
        if event.get("type") in ("barge_in", "latency", "interim"):
            self.emit({"type": "voice", **event})

    def _persist_line(self, speaker: Speaker, text: str, at: float) -> None:
        if self._call_id is None:
            return
        self.storage.add_transcript_line(
            self._call_id, TranscriptLine(speaker=speaker, text=text, started_at=at)
        )

    # ------------------------------------------------------------ operator actions
    async def post_order_to_1c(self, order_id: int) -> dict:
        order = self.storage.get_order(order_id)
        if order is None:
            raise ValueError(f"order {order_id} not found")
        call = self.storage.get_call(order.call_id) if order.call_id else None
        result = await self.one_c.create_lead_or_order(
            call.phone if call else "unknown", order.items,
            comment=order.comment, address=order.delivery_address,
        )
        number = result.get("number_1c") or f"LOCAL-{order_id}"
        self.storage.mark_order_posted(order_id, number)
        self.emit({"type": "order_posted", "order_id": order_id, "number_1c": number})
        return result

    async def resend_offer(self, order_id: int) -> None:
        """Hand-off to the company's WhatsApp/SMS gateway.

        Gateway adapters (WhatsApp Business API / SMS provider) subscribe to
        the ``offer_resent`` event; no third-party SDK is hard-wired here.
        """
        order = self.storage.get_order(order_id)
        if order is None:
            raise ValueError(f"order {order_id} not found")
        log.info("offer re-send requested for order %s", order_id)
        self.emit({"type": "offer_resent", "order_id": order_id,
                   "total": order.total})

    # ------------------------------------------------------------------ demo API
    async def simulate_utterance(self, text: str) -> str:
        """Drive the brain directly (UI demo / tests), bypassing STT."""
        return await self._on_user_text(text)


# --------------------------------------------------------------------- Qt wrapper
class EngineThread:  # pragma: no cover - exercised via UI smoke test
    """QThread hosting the asyncio event loop. Imported lazily so headless
    environments without PySide6 can still use CallEngine."""

    def __new__(cls, *args, **kwargs):
        from PySide6.QtCore import QThread, Signal  # noqa: PLC0415

        engine_holder: dict = {}

        class _Impl(QThread):
            event_received = Signal(dict)

            def __init__(self, settings, storage, data_dir):
                super().__init__()
                self._settings = settings
                self._storage = storage
                self._data_dir = data_dir
                self.loop: asyncio.AbstractEventLoop | None = None
                self.engine: CallEngine | None = None

            def run(self):
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.engine = CallEngine(self._settings, self._storage, self._data_dir)
                self.engine.add_listener(lambda evt: self.event_received.emit(evt))
                engine_holder["engine"] = self.engine

                async def _serve():
                    await self.engine.run()        # returns once the stop event is set
                    await self.engine.shutdown()    # same loop: graceful teardown

                self.loop.run_until_complete(_serve())

            def submit(self, coro):
                if not self.loop:
                    raise RuntimeError("engine thread not started")
                return asyncio.run_coroutine_threadsafe(coro, self.loop)

            def graceful_stop(self):
                if self.engine and self.loop:
                    # signal run() to return; the thread then runs shutdown() itself
                    self.loop.call_soon_threadsafe(self.engine._stop.set)
                if not self.wait(8000):
                    log.warning("engine thread did not finish in 8s, forcing quit")
                    self.terminate()

        return _Impl(*args, **kwargs)
