"""Anthropic (cloud) provider using the official SDK. The API key never leaves the backend."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.providers.base import AIProvider, ChatMessage, ProviderFailure, ProviderStatus, ToolCall, ToolSpec


def _map_error(exc: Exception) -> ProviderFailure:
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderFailure("Anthropic rejected the API key (authentication failed).", kind="auth")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderFailure("Anthropic denied permission for this request.", kind="permission")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderFailure("The requested Anthropic model was not found.", kind="model_unavailable")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderFailure("Anthropic rate limit reached. Wait and retry.", kind="rate_limited", retryable=True)
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderFailure("Anthropic request timed out.", kind="timeout", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderFailure("Cannot reach the Anthropic API (network error).", kind="unreachable", retryable=True)
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderFailure(f"Anthropic API error (HTTP {exc.status_code}).",
                               retryable=exc.status_code >= 500)
    return ProviderFailure(f"Anthropic request failed: {type(exc).__name__}")


def _anthropic_message(m: ChatMessage) -> dict[str, Any]:
    if not m.images:
        return {"role": m.role, "content": m.content}
    blocks: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": i.mime, "data": base64.b64encode(i.data).decode()}}
        for i in m.images
    ]
    blocks.append({"type": "text", "text": m.content or "(image)"})
    return {"role": m.role, "content": blocks}


def _anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert history; tool calls become tool_use blocks and consecutive tool results one user turn."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list) and all(
                    b.get("type") == "tool_result" for b in last["content"]):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append(_anthropic_message(m))
    return out


class AnthropicProvider(AIProvider):
    name = "anthropic"
    label = "Anthropic Claude (cloud)"
    kind = "cloud"

    def __init__(self, api_key: str | None, model: str, max_tokens: int = 4096, timeout: float = 120.0,
                 client_kwargs: dict[str, Any] | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client_kwargs = client_kwargs or {}  # injectable (e.g. http_client) for tests

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def default_model(self) -> str:
        return self.model

    def _client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key=self._api_key, timeout=self.timeout, max_retries=1,
                                        **self._client_kwargs)

    async def supports_images(self, model: str) -> bool | None:
        # Current Claude models accept image input; an unknown model name fails visibly at request time.
        return True if self.is_configured() else None

    async def supports_tools(self, model: str) -> bool | None:
        return True if self.is_configured() else None

    async def stream_turn(self, messages: list[ChatMessage], *, model: str, system: str,
                          tools: list[ToolSpec]) -> AsyncIterator[str | ToolCall]:
        if not self.is_configured():
            raise ProviderFailure("Anthropic is not configured (NOVA_ANTHROPIC_API_KEY missing).",
                                  kind="not_configured")
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": _anthropic_messages(messages),
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
        }
        if system:
            kwargs["system"] = system
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        yield event.text
                final = await stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    yield ToolCall(block.id, block.name, dict(block.input) if isinstance(block.input, dict) else {})
        except ProviderFailure:
            raise
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc

    async def status(self) -> ProviderStatus:
        st = ProviderStatus(name=self.name, label=self.label, kind="cloud",
                            configured=self.is_configured(), reachable=False, default_model=self.model)
        if not st.configured:
            st.error = "NOVA_ANTHROPIC_API_KEY is not set."
            return st
        try:
            client = self._client()
            page = await client.models.list(limit=50)
            st.models = [m.id for m in page.data]
            st.reachable = True
        except Exception as exc:  # noqa: BLE001 - mapped to a user-facing message
            st.error = _map_error(exc).message
        return st

    async def stream(self, messages: list[ChatMessage], *, model: str, system: str) -> AsyncIterator[str]:
        if not self.is_configured():
            raise ProviderFailure("Anthropic is not configured (NOVA_ANTHROPIC_API_KEY missing).",
                                  kind="not_configured")
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": _anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except ProviderFailure:
            raise
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
