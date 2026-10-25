"""Streaming chat-completions client (Groq / any OpenAI-compatible endpoint).

Handles SSE delta parsing, streamed tool-call assembly and cooperative
cancellation so barge-in can abort generation mid-token.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from ..config import LlmConfig

log = logging.getLogger(__name__)


class TurnCancelled(Exception):
    """Raised when the caller signalled barge-in before generation finished."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class AssistantMessage:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    def as_chat_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in self.tool_calls
            ]
        return msg


class LlmClient:
    def __init__(self, config: LlmConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = config
        timeout = httpx.Timeout(config.timeout_ms / 1000.0, connect=2.0)
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {config.api_key}",
                     "Content-Type": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       cancel: asyncio.Event | None = None) -> AssistantMessage:
        payload: dict = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        req = self._client.build_request("POST", "/chat/completions", json=payload)
        try:
            resp = await self._client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise LlmError(f"connection failed: {exc}") from exc
        try:
            resp.raise_for_status()
            return await self._consume_stream(resp, cancel)
        except httpx.HTTPStatusError as exc:
            body = await resp.aread()
            raise LlmError(f"HTTP {exc.response.status_code}: {body[:300]!r}") from exc
        finally:
            await resp.aclose()

    async def _consume_stream(self, resp: httpx.Response,
                              cancel: asyncio.Event | None) -> AssistantMessage:
        message = AssistantMessage()
        # tool_call index -> partial string buffer
        buffers: dict[int, dict] = {}

        async for line in resp.aiter_lines():
            if cancel is not None and cancel.is_set():
                raise TurnCancelled()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if text := delta.get("content"):
                message.content += text
            for tc_delta in delta.get("tool_calls", []) or []:
                idx = tc_delta.get("index", 0)
                buf = buffers.setdefault(idx, {"id": "", "name": "", "args": ""})
                fn = tc_delta.get("function", {})
                buf["id"] = tc_delta.get("id") or buf["id"]
                buf["name"] = fn.get("name") or buf["name"]
                buf["args"] += fn.get("arguments", "")

        for buf in buffers.values():
            try:
                args = json.loads(buf["args"]) if buf["args"].strip() else {}
            except json.JSONDecodeError:
                log.warning("malformed tool arguments, ignoring: %r", buf["args"])
                args = {}
            message.tool_calls.append(ToolCall(id=buf["id"] or "call", name=buf["name"], arguments=args))
        return message


class LlmError(RuntimeError):
    pass
