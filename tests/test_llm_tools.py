import asyncio
import json

import httpx
import pytest

from app.brain.llm import LlmClient, LlmError, TurnCancelled
from app.config import LlmConfig


def _sse(events: list[dict]) -> bytes:
    lines = []
    for event in events:
        lines.append("data: " + json.dumps(event, ensure_ascii=False))
    lines.append("data: [DONE]")
    return ("\n".join(lines) + "\n").encode()


def _client(handler) -> LlmClient:
    cfg = LlmConfig(base_url="http://llm.local/v1", api_key="k", model="m")
    return LlmClient(cfg, transport=httpx.MockTransport(handler))


async def test_stream_parses_text_and_streamed_tool_call():
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True and body["tools"]
        payload = _sse([
            {"choices": [{"delta": {"content": "Минутку, "}}]},
            {"choices": [{"delta": {"content": "проверяю."}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "tc1", "function": {
                    "name": "check_catalog_and_price", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{\"voltage\": 24"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ", \"stroke_mm\": 300}"}}]}}]},
        ])
        return httpx.Response(200, content=payload,
                              headers={"content-type": "text/event-stream"})

    message = await _client(handler).complete(
        [{"role": "user", "content": "привет"}], tools=[{"type": "function"}])
    assert message.content == "Минутку, проверяю."
    assert len(message.tool_calls) == 1
    call = message.tool_calls[0]
    assert call.id == "tc1" and call.name == "check_catalog_and_price"
    assert call.arguments == {"voltage": 24, "stroke_mm": 300}


async def test_barge_in_cancels_generation():
    def handler(request):
        return httpx.Response(200, content=_sse([
            {"choices": [{"delta": {"content": "x"}}]},
            {"choices": [{"delta": {"content": "y"}}]},
        ]))

    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(TurnCancelled):
        await _client(handler).complete([], cancel=cancel)


async def test_http_error_wrapped():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(LlmError):
        await _client(handler).complete([])
