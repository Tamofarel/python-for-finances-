"""Provider adapters tested against MOCKED HTTP transports.

These tests exercise NOVA's request/streaming/error-mapping code (and, for Anthropic, the official
SDK's parsing) without any network. They do NOT prove that a live Ollama or Anthropic service works.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import httpx2
import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ProviderFailure
from app.providers.ollama import OllamaProvider
from app.providers.registry import ProviderRegistry


def run(coro):
    return asyncio.run(coro)


async def collect(provider, model="m"):
    out = []
    async for chunk in provider.stream([ChatMessage("user", "hi")], model=model, system="sys"):
        out.append(chunk)
    return "".join(out)


# ---- Ollama ------------------------------------------------------------------------------
def ollama_with(handler):
    return OllamaProvider("http://ollama.test", "llama3.2", transport=httpx.MockTransport(handler))


def test_ollama_status_reachable_lists_models():
    p = ollama_with(lambda req: httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]}))
    st = run(p.status())
    assert st.configured and st.reachable and st.models == ["llama3.2:latest"] and st.error is None


def test_ollama_status_model_missing_is_reported():
    p = ollama_with(lambda req: httpx.Response(200, json={"models": [{"name": "mistral:7b"}]}))
    st = run(p.status())
    assert st.reachable and "not installed" in st.error and "never downloads" in st.error


def test_ollama_unreachable_is_not_connected():
    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    st = run(ollama_with(boom).status())
    assert st.configured and not st.reachable and "not reachable" in st.error


def test_ollama_stream_parses_ndjson_and_sends_system():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        lines = [{"message": {"content": "Hel"}}, {"message": {"content": "lo"}}, {"done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    assert run(collect(ollama_with(handler))) == "Hello"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["stream"] is True
    assert seen["body"]["keep_alive"] == "10m"


def test_ollama_missing_model_error():
    p = ollama_with(lambda req: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(ProviderFailure) as exc:
        run(collect(p, "ghost"))
    assert exc.value.kind == "model_unavailable" and "ollama pull ghost" in exc.value.message


def test_ollama_connect_error_maps_to_failure():
    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(ProviderFailure) as exc:
        run(collect(ollama_with(boom)))
    assert exc.value.kind == "unreachable" and exc.value.retryable


# ---- Anthropic (official SDK over a mocked transport) ------------------------------------------
def _sse(events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def anthropic_with(handler):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider("sk-ant-test-key-not-real", "claude-test", client_kwargs={"http_client": client})


def test_anthropic_not_configured():
    p = AnthropicProvider(None, "claude-test")
    st = run(p.status())
    assert not st.configured and not st.reachable and "NOVA_ANTHROPIC_API_KEY" in st.error
    with pytest.raises(ProviderFailure) as exc:
        run(collect(p))
    assert exc.value.kind == "not_configured"


def test_anthropic_stream_via_sdk():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers.get("x-api-key")
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}
        events = [
            ("message_start", {"type": "message_start", "message": msg}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "Bonjour "}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "Farel"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 2}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    assert run(collect(anthropic_with(handler), "claude-test")) == "Bonjour Farel"
    assert seen["body"]["system"] == "sys" and seen["body"]["stream"] is True
    assert seen["key"] == "sk-ant-test-key-not-real"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (429, "rate_limited"), (404, "model_unavailable")])
def test_anthropic_errors_are_mapped(status, kind):
    def handler(req):
        return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})
    with pytest.raises(ProviderFailure) as exc:
        run(collect(anthropic_with(handler)))
    assert exc.value.kind == kind
    assert "sk-ant" not in exc.value.message


def test_anthropic_status_lists_models():
    def handler(req):
        return httpx2.Response(200, json={"data": [{"id": "claude-a", "type": "model", "display_name": "A",
                                                    "created_at": "2026-01-01T00:00:00Z"}],
                                          "has_more": False, "first_id": "claude-a", "last_id": "claude-a"})
    st = run(anthropic_with(handler).status())
    assert st.reachable and st.models == ["claude-a"]


def test_registry_distinguishes_configured_and_reachable():
    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    reg = ProviderRegistry([ollama_with(boom), AnthropicProvider(None, "c")])
    sts = {s.name: s for s in run(reg.statuses())}
    assert sts["ollama"].configured and not sts["ollama"].reachable
    assert not sts["anthropic"].configured and not sts["anthropic"].reachable


# ---- tool calling ---------------------------------------------------------------------------------------
from app.providers.base import ToolCall, ToolSpec  # noqa: E402

SPOTIFY_SPEC = ToolSpec("spotify_control", "Control Spotify.", {
    "type": "object", "properties": {"action": {"type": "string"}, "query": {"type": "string"}}, "required": ["action"]})


async def turn(provider, messages, model="m"):
    out = []
    async for event in provider.stream_turn(messages, model=model, system="sys", tools=[SPOTIFY_SPEC]):
        out.append(event)
    return out


def test_ollama_turn_offers_tools_and_returns_the_models_tool_call():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        call = {"function": {"name": "spotify_control", "arguments": {"action": "play_track", "query": "Vie de Pirate"}}}
        lines = [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}, {"done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    events = run(turn(ollama_with(handler), [ChatMessage("user", "play Vie de Pirate")]))
    assert seen["body"]["tools"] == [{"type": "function", "function": {
        "name": "spotify_control", "description": "Control Spotify.", "parameters": SPOTIFY_SPEC.parameters}}]
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1 and calls[0].name == "spotify_control"
    assert calls[0].arguments == {"action": "play_track", "query": "Vie de Pirate"}


def test_ollama_turn_streams_text_and_accepts_string_arguments():
    def handler(req):
        call = {"function": {"name": "spotify_control", "arguments": '{"action": "pause"}'}}
        lines = [{"message": {"content": "Sure"}}, {"message": {"content": "", "tool_calls": [call]}}, {"done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    events = run(turn(ollama_with(handler), [ChatMessage("user", "pause")]))
    assert events[0] == "Sure" and events[1].arguments == {"action": "pause"}


def test_ollama_turn_sends_tool_calls_and_results_back_to_the_model():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=json.dumps({"message": {"content": "Done."}, "done": True}).encode())

    call = ToolCall("call_1", "spotify_control", {"action": "pause"})
    history = [ChatMessage("user", "pause"), ChatMessage("assistant", "", tool_calls=(call,)),
               ChatMessage("tool", '{"status": "succeeded"}', tool_call_id="call_1", name="spotify_control")]
    assert run(turn(ollama_with(handler), history)) == ["Done."]
    msgs = seen["body"]["messages"]
    assert msgs[2] == {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "spotify_control", "arguments": {"action": "pause"}}}]}
    assert msgs[3] == {"role": "tool", "content": '{"status": "succeeded"}', "tool_name": "spotify_control"}


def test_ollama_reports_tool_support_from_model_capabilities():
    caps = {"llama3.2": ["completion", "tools"], "phi": ["completion"]}

    def handler(req):
        return httpx.Response(200, json={"capabilities": caps[json.loads(req.content)["model"]]})

    p = ollama_with(handler)
    assert run(p.supports_tools("llama3.2")) is True and run(p.supports_tools("phi")) is False


def test_ollama_model_without_tool_support_is_a_specific_failure():
    p = ollama_with(lambda req: httpx.Response(400, json={"error": "registry.ollama.ai/library/phi:latest does not support tools"}))
    with pytest.raises(ProviderFailure) as exc:
        run(turn(p, [ChatMessage("user", "hi")], model="phi"))
    assert exc.value.kind == "tools_unsupported"


def test_anthropic_turn_offers_tools_and_parses_tool_use():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}
        events = [
            ("message_start", {"type": "message_start", "message": msg}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "tool_use", "id": "toolu_1", "name": "spotify_control",
                                                       "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": '{"action": "play_track", "query": "Vie de Pirate"}'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                               "usage": {"output_tokens": 9}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    events = run(turn(anthropic_with(handler), [ChatMessage("user", "play Vie de Pirate")], model="claude-test"))
    assert seen["body"]["tools"] == [{"name": "spotify_control", "description": "Control Spotify.",
                                      "input_schema": SPOTIFY_SPEC.parameters}]
    assert events == [ToolCall("toolu_1", "spotify_control", {"action": "play_track", "query": "Vie de Pirate"})]


def test_anthropic_tool_results_are_sent_as_one_user_turn():
    from app.providers.anthropic_provider import _anthropic_messages

    calls = (ToolCall("t1", "a", {}), ToolCall("t2", "b", {"x": 1}))
    out = _anthropic_messages([ChatMessage("user", "go"), ChatMessage("assistant", "On it.", tool_calls=calls),
                               ChatMessage("tool", "r1", tool_call_id="t1", name="a"),
                               ChatMessage("tool", "r2", tool_call_id="t2", name="b")])
    assert out[1] == {"role": "assistant", "content": [{"type": "text", "text": "On it."},
                                                       {"type": "tool_use", "id": "t1", "name": "a", "input": {}},
                                                       {"type": "tool_use", "id": "t2", "name": "b", "input": {"x": 1}}]}
    assert out[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
                                                  {"type": "tool_result", "tool_use_id": "t2", "content": "r2"}]}
