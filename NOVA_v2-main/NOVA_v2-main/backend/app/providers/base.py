"""Provider abstraction. The conversation engine only depends on this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ImageInput:
    """An image attached to a user message (validated PNG/JPEG/GIF/WebP bytes)."""

    mime: str
    data: bytes


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool as offered to a model: name, description and JSON Schema of its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by a model. It is only a request: NOVA validates and decides whether it runs."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    images: tuple[ImageInput, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()  # assistant turns that requested tools
    tool_call_id: str | None = None  # role "tool": which call this result answers
    name: str | None = None  # role "tool": the tool's name


@dataclass
class ProviderStatus:
    name: str
    label: str
    kind: Literal["local", "cloud"]
    configured: bool  # required configuration exists
    reachable: bool  # a live check succeeded just now
    models: list[str] = field(default_factory=list)  # models reported by the provider itself
    default_model: str = ""
    error: str | None = None  # actual failure observed, if any
    supports_streaming: bool = True


class ProviderFailure(Exception):
    """A real failure reported by (or while contacting) a provider. Never replaced by fake output."""

    def __init__(self, message: str, *, kind: str = "provider_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retryable = retryable


class AIProvider(ABC):
    name: str
    label: str
    kind: Literal["local", "cloud"]

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def default_model(self) -> str: ...

    @abstractmethod
    async def status(self) -> ProviderStatus: ...

    @abstractmethod
    def stream(self, messages: list[ChatMessage], *, model: str, system: str) -> AsyncIterator[str]:
        """Yield text deltas. Raise ProviderFailure on any provider-side error."""

    async def supports_images(self, model: str) -> bool | None:
        """True/False when the provider can tell whether `model` reads images; None when unknown."""
        return None

    async def supports_tools(self, model: str) -> bool | None:
        """True/False when the provider can tell whether `model` can call tools; None when unknown."""
        return None

    async def stream_turn(self, messages: list[ChatMessage], *, model: str, system: str,
                          tools: list[ToolSpec]) -> AsyncIterator[str | ToolCall]:
        """One model turn with tools offered: yields text deltas and, at the end, the tool calls it requested."""
        raise ProviderFailure(f"{self.label} cannot call tools.", kind="tools_unsupported")
        yield ""  # pragma: no cover - makes this an async generator

    async def complete(self, messages: list[ChatMessage], *, model: str, system: str) -> str:
        parts: list[str] = []
        async for chunk in self.stream(messages, model=model, system=system):
            parts.append(chunk)
        return "".join(parts)
