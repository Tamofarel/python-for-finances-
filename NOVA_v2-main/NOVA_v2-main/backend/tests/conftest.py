"""Test fixtures.

External AI services are replaced by `ScriptedProvider`, a deterministic TEST-ONLY provider.
Tests that use it prove NOVA's own logic, never that a live provider works.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.providers.base import AIProvider, ChatMessage, ProviderFailure, ProviderStatus, ToolCall, ToolSpec
from app.providers.registry import ProviderRegistry
from app.research import DisabledResearch, ResearchProvider


class ScriptedProvider(AIProvider):
    name = "scripted"
    label = "Scripted test provider"
    kind = "local"

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.fail_with: ProviderFailure | None = None
        self.calls: list[dict] = []
        self.delay = 0.0
        self.vision: bool | None = None
        # Tool calling: each scripted turn is a list of text deltas and ToolCalls. None = tools unsupported.
        self.tools_supported: bool | None = None
        self.turns: list[list[str | ToolCall]] = []

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str:
        return "scripted-1"

    async def supports_images(self, model: str) -> bool | None:
        return self.vision

    async def status(self) -> ProviderStatus:
        ok = self.is_configured()
        return ProviderStatus(self.name, self.label, "local", ok, ok, ["scripted-1"] if ok else [], "scripted-1")

    async def stream(self, messages: list[ChatMessage], *, model: str, system: str) -> AsyncIterator[str]:
        self.calls.append({"messages": messages, "model": model, "system": system})
        if self.delay:
            import asyncio
            await asyncio.sleep(self.delay)
        if self.fail_with:
            raise self.fail_with
        reply = self.replies.pop(0) if self.replies else f"echo: {messages[-1].content}"
        for i in range(0, len(reply), 7):
            yield reply[i: i + 7]

    async def supports_tools(self, model: str) -> bool | None:
        return self.tools_supported

    async def stream_turn(self, messages: list[ChatMessage], *, model: str, system: str,
                          tools: list[ToolSpec]):
        self.calls.append({"messages": messages, "model": model, "system": system, "tools": tools})
        if self.tools_supported is not True:
            raise ProviderFailure("Scripted provider: tools unsupported.", kind="tools_unsupported")
        if self.fail_with:
            raise self.fail_with
        events = self.turns.pop(0) if self.turns else [f"echo: {messages[-1].content}"]
        for event in events:
            yield event


class UnconfiguredProvider(ScriptedProvider):
    name = "unconfigured"

    def is_configured(self) -> bool:
        return False


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", default_provider="scripted", environment="test",
                    anthropic_api_key=None, command_timeout_seconds=20,
                    _env_file=None)  # type: ignore[call-arg]


def make_client(settings: Settings, provider: AIProvider, research: ResearchProvider | None = None) -> TestClient:
    registry = ProviderRegistry([provider, UnconfiguredProvider()])
    app = create_app(settings, registry=registry, research=research or DisabledResearch(),
                     frontend_dist=settings.data_dir / "no-frontend")
    client = TestClient(app)
    client.headers["X-NOVA-Client"] = "web"
    return client


@pytest.fixture
def client(settings: Settings, provider: ScriptedProvider) -> TestClient:
    return make_client(settings, provider)


def setup_owner(client: TestClient, username: str = "farel", password: str = "correct-horse-battery") -> dict:
    code = client.app.state.nova.setup_code  # type: ignore[attr-defined]
    r = client.post("/api/auth/setup", json={"username": username, "password": password, "setup_code": code})
    assert r.status_code == 201, r.text
    body = r.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    setup_owner(client)
    return client


@pytest.fixture
def project(authed: TestClient) -> dict:
    r = authed.post("/api/projects", json={"name": "Demo", "description": "test project"})
    assert r.status_code == 201, r.text
    return r.json()


def parse_sse(text: str) -> list[tuple[str, dict]]:
    import json

    events = []
    for block in text.strip().split("\n\n"):
        ev, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            events.append((ev, json.loads(data)))
    return events
