"""Ollama (local) provider using its HTTP API. Never pulls models automatically."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator

import httpx

from app.core.logging import get_logger
from app.providers.base import AIProvider, ChatMessage, ProviderFailure, ProviderStatus, ToolCall, ToolSpec

log = get_logger("nova.provider.ollama")


def _ollama_message(m: ChatMessage) -> dict:
    if m.role == "tool":
        return {"role": "tool", "content": m.content, "tool_name": m.name or ""}
    out: dict = {"role": m.role, "content": m.content}
    if m.images:
        out["images"] = [base64.b64encode(i.data).decode() for i in m.images]
    if m.tool_calls:
        out["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
    return out


def _arguments(raw: object) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class OllamaProvider(AIProvider):
    name = "ollama"
    label = "Ollama (local)"
    kind = "local"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0, keep_alive: str = "10m",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._transport = transport  # injectable for tests

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or self.timeout,
                                 transport=self._transport)

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def default_model(self) -> str:
        return self.model

    async def list_models(self) -> list[str]:
        async with self._client(timeout=5.0) as client:
            resp = await client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))

    async def supports_images(self, model: str) -> bool | None:
        """Ollama 0.6+ reports model capabilities ("vision") from /api/show."""
        try:
            async with self._client(timeout=5.0) as client:
                resp = await client.post("/api/show", json={"model": model})
                if resp.status_code >= 400:
                    return None
                caps = resp.json().get("capabilities")
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(caps, list):
            return None
        return "vision" in caps

    async def supports_tools(self, model: str) -> bool | None:
        """Ollama reports "tools" among a model's capabilities when the model can call tools."""
        try:
            async with self._client(timeout=5.0) as client:
                resp = await client.post("/api/show", json={"model": model})
                if resp.status_code >= 400:
                    return None
                caps = resp.json().get("capabilities")
        except (httpx.HTTPError, ValueError):
            return None
        return "tools" in caps if isinstance(caps, list) else None

    async def status(self) -> ProviderStatus:
        st = ProviderStatus(name=self.name, label=self.label, kind="local",
                            configured=self.is_configured(), reachable=False, default_model=self.model)
        if not st.configured:
            st.error = "NOVA_OLLAMA_BASE_URL is not set."
            return st
        try:
            st.models = await self.list_models()
            st.reachable = True
            if not any(m == self.model or m.split(":")[0] == self.model for m in st.models):
                st.error = (f"Model '{self.model}' is not installed. Run `ollama pull {self.model}` "
                            "yourself if you want it (NOVA never downloads models automatically).")
        except httpx.HTTPError as exc:
            st.error = f"Ollama is not reachable at {self.base_url} ({type(exc).__name__})."
        except ValueError:
            st.error = "Ollama returned an unexpected response."
        return st

    async def stream(self, messages: list[ChatMessage], *, model: str, system: str) -> AsyncIterator[str]:
        async for event in self._chat(messages, model=model, system=system, tools=None):
            if isinstance(event, str):
                yield event

    async def stream_turn(self, messages: list[ChatMessage], *, model: str, system: str,
                          tools: list[ToolSpec]) -> AsyncIterator[str | ToolCall]:
        async for event in self._chat(messages, model=model, system=system, tools=tools):
            yield event

    async def _chat(self, messages: list[ChatMessage], *, model: str, system: str,
                    tools: list[ToolSpec] | None) -> AsyncIterator[str | ToolCall]:
        payload: dict = {
            "model": model,
            "stream": True,
            "keep_alive": self.keep_alive,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [_ollama_message(m) for m in messages],
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.parameters}} for t in tools]
        calls = 0
        started = time.monotonic()
        first_output: float | None = None
        try:
            async with self._client() as client:
                async with client.stream("POST", "/api/chat", json=payload) as resp:
                    if resp.status_code == 404:
                        await resp.aread()
                        raise ProviderFailure(
                            f"Model '{model}' is not available in Ollama. Install it with `ollama pull {model}`.",
                            kind="model_unavailable")
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode(errors="replace")[:300]
                        if "does not support tools" in body:
                            raise ProviderFailure(f"The Ollama model '{model}' cannot call tools. Choose a model with "
                                                  "tool support, such as llama3.2 or mistral.", kind="tools_unsupported")
                        raise ProviderFailure(f"Ollama error {resp.status_code}: {body}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get("error"):
                            raise ProviderFailure(f"Ollama error: {data['error']}")
                        message = data.get("message") or {}
                        chunk = message.get("content") or ""
                        if chunk:
                            if first_output is None:
                                first_output = time.monotonic()
                                log.info("model first output", extra={"extra_data": {
                                    "provider": self.name, "model": model, "tools": bool(tools),
                                    "ttft_ms": round((first_output - started) * 1000)}})
                            yield chunk
                        for call in message.get("tool_calls") or []:
                            fn = (call or {}).get("function") or {}
                            if fn.get("name"):
                                calls += 1
                                if first_output is None:
                                    first_output = time.monotonic()
                                    log.info("model first output", extra={"extra_data": {
                                        "provider": self.name, "model": model, "tools": bool(tools),
                                        "ttft_ms": round((first_output - started) * 1000)}})
                                yield ToolCall(str(call.get("id") or f"call_{calls}"), str(fn["name"]),
                                               _arguments(fn.get("arguments")))
                        if data.get("done"):
                            break
            log.info("model request finished", extra={"extra_data": {
                "provider": self.name, "model": model, "tools": bool(tools),
                "duration_ms": round((time.monotonic() - started) * 1000), "tool_calls": calls}})
        except httpx.ConnectError as exc:
            log.info("model request failed", extra={"extra_data": {
                "provider": self.name, "model": model, "kind": "unreachable",
                "duration_ms": round((time.monotonic() - started) * 1000)}})
            raise ProviderFailure(f"Cannot connect to Ollama at {self.base_url}. Is it running?",
                                  kind="unreachable", retryable=True) from exc
        except httpx.TimeoutException as exc:
            log.info("model request failed", extra={"extra_data": {
                "provider": self.name, "model": model, "kind": "timeout",
                "duration_ms": round((time.monotonic() - started) * 1000)}})
            raise ProviderFailure("Ollama timed out.", kind="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            log.info("model request failed", extra={"extra_data": {
                "provider": self.name, "model": model, "kind": "http_error",
                "duration_ms": round((time.monotonic() - started) * 1000)}})
            raise ProviderFailure(f"Ollama request failed: {type(exc).__name__}", retryable=True) from exc
