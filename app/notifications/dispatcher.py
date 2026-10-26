"""Outbound quote delivery: Telegram Bot API, generic webhook, WhatsApp gateway.

All transports speak plain httpx so they work behind factory proxies and are
trivially mockable in tests. Every channel is optional: an unconfigured
dispatcher is a safe no-op and reports which channel was used.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from ..config import NotificationConfig
from ..core.models import Order

log = logging.getLogger(__name__)


class NotificationError(Exception):
    pass


class NotificationDispatcher:
    def __init__(self, config: NotificationConfig,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = config
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=2.0),
                                         transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "NotificationDispatcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ public
    async def send_quote(self, order: Order, pdf_path: str | Path, *,
                         phone: str = "", caption: str = "") -> list[dict]:
        """Dispatch the PDF over every configured channel. Returns receipts."""
        receipts: list[dict] = []
        if self.cfg.telegram_bot_token and self.cfg.telegram_chat_id:
            receipts.append(await self.send_telegram(order, pdf_path, caption))
        if self.cfg.generic_webhook_url:
            receipts.append(await self.send_webhook(order, pdf_path, phone))
        if self.cfg.whatsapp_endpoint and self.cfg.whatsapp_token:
            receipts.append(await self.send_whatsapp(order, pdf_path, phone, caption))
        if not receipts:
            log.info("notification channels unconfigured; PDF kept locally: %s", pdf_path)
        return receipts

    async def send_telegram(self, order: Order, pdf_path: str | Path,
                            caption: str = "") -> dict:
        url = (f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}"
               "/sendDocument")
        with Path(pdf_path).open("rb") as fh:
            data = {
                "chat_id": self.cfg.telegram_chat_id,
                "caption": caption or self._default_caption(order),
            }
            resp = await self._client.post(
                url, data=data,
                files={"document": (Path(pdf_path).name, fh, "application/pdf")},
            )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise NotificationError(f"telegram error: {body}")
        return {"channel": "telegram", "ok": True, "message_id":
                body.get("result", {}).get("message_id")}

    async def send_webhook(self, order: Order, pdf_path: str | Path,
                           phone: str = "") -> dict:
        with Path(pdf_path).open("rb") as fh:
            payload = {
                "order_number": order.number_1c or f"LOCAL-{order.id}",
                "phone": phone,
                "total": order.total,
                "items": [
                    {"article": i.article, "name": i.name,
                     "quantity": i.quantity, "price": i.price, "sum": i.sum}
                    for i in order.items
                ],
            }
            resp = await self._client.post(
                self.cfg.generic_webhook_url,
                data={"payload": json.dumps(payload, ensure_ascii=False)},
                files={"pdf": (Path(pdf_path).name, fh, "application/pdf")},
            )
        resp.raise_for_status()
        return {"channel": "webhook", "ok": True, "status": resp.status_code}

    async def send_whatsapp(self, order: Order, pdf_path: str | Path,
                            phone: str, caption: str = "") -> dict:
        """Green-API-compatible sendFile endpoint."""
        url = self.cfg.whatsapp_endpoint.rstrip("/")
        with Path(pdf_path).open("rb") as fh:
            files = {"file": (Path(pdf_path).name, fh, "application/pdf")}
            data = {
                "chatId": phone,
                "caption": caption or self._default_caption(order),
                "fileName": Path(pdf_path).name,
            }
            resp = await self._client.post(url, data=data, files=files)
        resp.raise_for_status()
        return {"channel": "whatsapp", "ok": True, "status": resp.status_code}

    @staticmethod
    def _default_caption(order: Order) -> str:
        number = order.number_1c or f"LOCAL-{order.id}"
        lines = [f"КП / счёт по заказу {number}"]
        for item in order.items:
            lines.append(f"• {item.name} — {item.quantity:g} × {item.price:,.0f} ₽")
        lines.append(f"Итого: {order.total:,.0f} ₽".replace(",", " "))
        return "\n".join(lines)
