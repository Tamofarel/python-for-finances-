import asyncio
import threading
import time

from pydantic import Field
from sqlalchemy import select

from app.models import AuditEvent
from app.tools.registry import PermissionLevel, Tool, ToolContext, ToolInput, ToolRegistry
from tests.conftest import setup_owner


class EmptyInput(ToolInput):
    pass


class LevelInput(ToolInput):
    level: int = Field(ge=0, le=10)


def done(_args, _ctx):
    return "succeeded", "done", {}


def test_tool_registry_lists_only_registered_schema_and_checks_args():
    registry = ToolRegistry([Tool("safe", "A test action", EmptyInput, PermissionLevel.ROUTINE, True, done)])
    schema = registry.schemas()[0]
    assert schema["name"] == "safe" and schema["risk"] == "reversible"
    assert registry.execute("unknown", {}).status == "denied"
    assert registry.execute("safe", {"untrusted": "field"}).status == "denied"
    assert registry.execute("safe", {}).status == "succeeded"


def test_invalid_arguments_are_rejected_with_a_correctable_reason_and_never_reach_the_handler():
    called = []

    def handler(args, _ctx):
        called.append(args)
        return "succeeded", "ok", {}

    registry = ToolRegistry([Tool("dim", "Set a level", LevelInput, PermissionLevel.ROUTINE, True, handler)])
    result = registry.execute("dim", {"level": 99})
    assert result.status == "denied" and result.data["reason"] == "invalid_arguments"
    assert "level" in result.message
    assert not called


def test_disabled_and_protected_tools_do_not_execute():
    called = []

    def handler(_args, _ctx):
        called.append(True)
        return "succeeded", "done", {}

    registry = ToolRegistry([
        Tool("disabled", "off", EmptyInput, PermissionLevel.ROUTINE, False, handler, unavailable_reason="Turned off."),
        Tool("protected", "approval needed", EmptyInput, PermissionLevel.APPROVAL, True, handler),
    ])
    disabled = registry.execute("disabled", {})
    assert disabled.status == "unavailable" and "Turned off." in disabled.message
    assert registry.execute("protected", {}).status == "denied"
    assert not called
    # Only an approval granted on screen (passed in by the approval endpoint) lets it run.
    assert registry.execute("protected", {}, approved=True).status == "succeeded"
    assert called == [True]


def test_per_call_risk_classification_can_require_approval():
    registry = ToolRegistry([Tool("device", "d", LevelInput, PermissionLevel.ROUTINE, True, done,
                                  classify=lambda a: PermissionLevel.APPROVAL if a.level > 5 else PermissionLevel.ROUTINE)])
    assert registry.permission_for("device", {"level": 2}) == PermissionLevel.ROUTINE
    assert registry.permission_for("device", {"level": 8}) == PermissionLevel.APPROVAL
    assert registry.execute("device", {"level": 2}).status == "succeeded"
    assert registry.execute("device", {"level": 8}).status == "denied"


def test_handler_exceptions_become_failures_not_success():
    def boom(_args, _ctx):
        raise RuntimeError("secret detail")

    result = ToolRegistry([Tool("boom", "b", EmptyInput, PermissionLevel.ROUTINE, True, boom)]).execute("boom", {})
    assert result.status == "failed" and "secret detail" not in result.message


def test_unknown_handler_status_is_reported_as_failure():
    registry = ToolRegistry([Tool("odd", "o", EmptyInput, PermissionLevel.ROUTINE, True,
                                  lambda _a, _c: ("probably_fine", "?", {}))])
    assert registry.execute("odd", {}).status == "failed"


def test_cooperative_cancellation_stops_a_running_tool():
    started = threading.Event()

    def slow(_args, ctx):
        started.set()
        ctx.sleep(30)
        return "succeeded", "should not get here", {}

    registry = ToolRegistry([Tool("slow", "s", EmptyInput, PermissionLevel.ROUTINE, True, slow, timeout_s=60)])
    ctx = ToolContext()
    threading.Timer(0.2, ctx.cancel).start()
    t0 = time.monotonic()
    result = registry.execute("slow", {}, ctx)
    assert result.status == "cancelled"
    assert time.monotonic() - t0 < 5


def test_async_run_enforces_the_tool_deadline_even_if_the_handler_ignores_it():
    release = threading.Event()

    def stubborn(_args, _ctx):
        release.wait(10)  # does not check the context
        return "succeeded", "late", {}

    registry = ToolRegistry([Tool("stubborn", "s", EmptyInput, PermissionLevel.ROUTINE, True, stubborn, timeout_s=0.3)])
    ctx = ToolContext()

    async def scenario():
        t0 = time.monotonic()
        result = await registry.run("stubborn", {}, ctx)
        elapsed = time.monotonic() - t0
        release.set()  # let the worker thread end so the event loop can close
        return result, elapsed

    result, elapsed = asyncio.run(scenario())
    assert result.status == "timed_out"
    assert ctx.cancelled  # the handler is told to stop as soon as it checks
    assert elapsed < 3


def test_context_deadline_raises_timeout_inside_cooperative_tools():
    def waits(_args, ctx):
        ctx.sleep(5)
        return "succeeded", "x", {}

    registry = ToolRegistry([Tool("waits", "w", EmptyInput, PermissionLevel.ROUTINE, True, waits, timeout_s=0.2)])
    result = asyncio.run(registry.run("waits", {}, ToolContext()))
    assert result.status == "timed_out"


def test_paused_registry_refuses_control_but_allows_observation():
    registry = ToolRegistry([
        Tool("look", "observe", EmptyInput, PermissionLevel.READ, True, done),
        Tool("act", "control", EmptyInput, PermissionLevel.ROUTINE, True, done),
    ])
    ctx = ToolContext(paused=True)
    assert registry.execute("look", {}, ctx).status == "succeeded"
    stopped = registry.execute("act", {}, ctx)
    assert stopped.status == "denied" and "emergency stop" in stopped.message.lower()


def test_tool_api_is_authenticated_csrf_protected_and_audited(client):
    assert client.get("/api/tools").status_code == 401
    setup_owner(client)
    csrf = client.headers["X-CSRF-Token"]
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/tools/execute", json={"name": "spotify_control", "arguments": {"action": "open"}}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf

    catalog = client.get("/api/tools").json()
    assert "spotify_control" in [t["name"] for t in catalog]
    spotify = next(t for t in catalog if t["name"] == "spotify_control")
    assert spotify["enabled"] is False and spotify["unavailable_reason"]
    result = client.post("/api/tools/execute", json={"name": "spotify_control", "arguments": {"action": "open"}})
    assert result.status_code == 200
    assert result.json()["status"] in ("unavailable", "succeeded")
    db = client.app.state.nova.db.SessionLocal()
    try:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "tool.spotify_control"))
        assert event is not None
        assert "query" not in (event.details or {})
    finally:
        db.close()
