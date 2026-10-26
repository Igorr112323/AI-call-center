import httpx
import pytest

from app.config import NotificationConfig
from app.core.models import Order, OrderItem
from app.notifications.dispatcher import NotificationDispatcher


def _order() -> Order:
    return Order(
        id=3, number_1c="ЛОКАЛ-1",
        items=[OrderItem("ACT-24-300", "Актуатор 24В", 1, 11500)],
    )


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "KP.pdf"
    path.write_bytes(b"%PDF-1.7 fake payload")
    return path


async def test_telegram_send_document_multipart(pdf):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["fields"] = dict(request.content.fields) if hasattr(request.content, "fields") else {}
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    cfg = NotificationConfig(telegram_bot_token="tok", telegram_chat_id="100500")
    dispatcher = NotificationDispatcher(cfg, transport=httpx.MockTransport(handler))
    receipt = await dispatcher.send_quote(_order(), pdf, phone="+79180000000")
    assert receipt == [{"channel": "telegram", "ok": True, "message_id": 42}]
    assert "api.telegram.org/bottok/sendDocument" in captured["url"]
    await dispatcher.aclose()


async def test_generic_webhook_and_whatsapp(pdf):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("content-type", "")))
        return httpx.Response(200, json={"ok": True})

    cfg = NotificationConfig(
        generic_webhook_url="http://hooks.local/quote",
        whatsapp_endpoint="http://wa.local/green-api/sendFile",
        whatsapp_token="wa-token",
    )
    dispatcher = NotificationDispatcher(cfg, transport=httpx.MockTransport(handler))
    receipts = await dispatcher.send_quote(_order(), pdf, phone="+79180000000")
    channels = {r["channel"] for r in receipts}
    assert channels == {"webhook", "whatsapp"}
    assert any("/quote" in path for path, _ in seen)
    assert any("sendFile" in path for path, _ in seen)
    await dispatcher.aclose()


async def test_unconfigured_dispatcher_is_noop(pdf):
    dispatcher = NotificationDispatcher(
        NotificationConfig(), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    receipts = await dispatcher.send_quote(_order(), pdf)
    assert receipts == []
    await dispatcher.aclose()


async def test_telegram_api_error_raises(pdf):
    def handler(request):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    cfg = NotificationConfig(telegram_bot_token="bad", telegram_chat_id="1")
    dispatcher = NotificationDispatcher(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await dispatcher.send_telegram(_order(), pdf)
    await dispatcher.aclose()
