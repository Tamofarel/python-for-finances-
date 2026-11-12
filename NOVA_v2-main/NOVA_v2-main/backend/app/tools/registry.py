"""Typed, explicit tool registry. Model-facing code may only dispatch registered tools.

Every tool declares a risk class:
- READ ("observe"): reads state, changes nothing.
- ROUTINE ("reversible"): a reversible change such as playing music, opening an app or turning on a light.
- APPROVAL ("safety-critical"): consequential or potentially hazardous (locks, heating, machinery, commands). These
  never run from a model or voice request; they become an approval request that the owner confirms on screen.

Handlers receive a ToolContext that carries the deadline and a cancellation flag; long-running handlers call
`ctx.check()` / `ctx.sleep()` so that Stop, barge-in, the emergency stop and deadlines take effect promptly.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class PermissionLevel(StrEnum):
    READ = "read"
    ROUTINE = "routine"
    APPROVAL = "approval"


RISK_LABELS = {PermissionLevel.READ: "observe", PermissionLevel.ROUTINE: "reversible",
               PermissionLevel.APPROVAL: "safety-critical"}

# succeeded: done and verified where the integration can verify · unverified: the request was accepted but the
# outcome could not be confirmed · needs_input: ambiguous, the owner must choose · needs_authorization: an account
# must be connected first · awaiting_approval: queued for on-screen approval · the rest are failures.
STATUSES = frozenset({"succeeded", "unverified", "needs_input", "needs_authorization", "awaiting_approval",
                      "unavailable", "denied", "failed", "timed_out", "cancelled"})


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCancelled(Exception):
    """Raised inside a handler when the owner stopped the action."""


class ToolDeadline(Exception):
    """Raised inside a handler when its deadline passed."""


@dataclass(eq=False)  # compared and hashed by identity: running contexts are tracked in sets
class ToolContext:
    owner_id: int | None = None
    deadline: float = math.inf  # time.monotonic() value
    paused: bool = False  # emergency stop: control actions are refused, observation still works
    workspace_root: Path | None = None  # the conversation's project workspace, when one is attached
    project_id: int | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise ToolCancelled()
        if time.monotonic() >= self.deadline:
            raise ToolDeadline()

    def sleep(self, seconds: float) -> None:
        """Wait, but wake up immediately on cancellation; raise when cancelled or past the deadline."""
        self.cancel_event.wait(max(0.0, min(seconds, self.remaining())))
        self.check()

    def narrowed(self, seconds: float) -> ToolContext:
        """The same context with a deadline no later than `seconds` from now (shares the cancel flag)."""
        return ToolContext(self.owner_id, min(self.deadline, time.monotonic() + seconds), self.paused,
                           self.workspace_root, self.project_id, self.cancel_event)


@dataclass(frozen=True)
class ToolResult:
    status: str
    message: str
    data: dict[str, Any]
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


Handler = Callable[[Any, ToolContext], tuple[str, str, dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[ToolInput]
    permission: PermissionLevel
    enabled: bool
    handler: Handler
    timeout_s: float = 20.0
    unavailable_reason: str | None = None
    # Per-call risk (for example a light is reversible but a lock is safety-critical). Can only be stricter.
    classify: Callable[[Any], PermissionLevel] | None = None
    # For calls that need approval: the approval request to queue, as (kind, params). Raises to refuse the call.
    # Without it, approval-level calls are queued as a generic "tool_call" that re-runs this exact call once approved.
    approval: Callable[[Any, ToolContext], tuple[str, dict[str, Any]]] | None = None


_ORDER = {PermissionLevel.READ: 0, PermissionLevel.ROUTINE: 1, PermissionLevel.APPROVAL: 2}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def enabled_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if t.enabled]

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "permission": t.permission.value,
                 "risk": RISK_LABELS[t.permission], "enabled": t.enabled,
                 "unavailable_reason": None if t.enabled else (t.unavailable_reason or "Disabled."),
                 "timeout_s": t.timeout_s, "parameters": t.args.model_json_schema()}
                for t in self._tools.values()]

    def parse(self, name: str, arguments: dict[str, Any]) -> tuple[Tool, ToolInput] | ToolResult:
        """Look up and validate a call. Returns the tool and its parsed arguments, or a refusal."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult("denied", f"There is no registered tool named {name!r}.", {"reason": "unknown_tool"}, 0)
        if not tool.enabled:
            reason = tool.unavailable_reason or "It is disabled."
            return ToolResult("unavailable", f"{name} is not available: {reason}", {"reason": "disabled"}, 0)
        try:
            return tool, tool.args.model_validate(arguments)
        except (ValidationError, TypeError) as exc:
            return ToolResult("denied", "Invalid arguments: " + _describe(exc), {"reason": "invalid_arguments"}, 0)

    def permission_for(self, name: str, arguments: dict[str, Any]) -> PermissionLevel | None:
        parsed = self.parse(name, arguments)
        if isinstance(parsed, ToolResult):
            return None
        return _permission(*parsed)

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext | None = None, *,
                approved: bool = False) -> ToolResult:
        """Validate and run one call synchronously. Failures become results; nothing raises."""
        started = time.monotonic()
        ctx = ctx or ToolContext()
        parsed = self.parse(name, arguments)
        if isinstance(parsed, ToolResult):
            return _with_time(parsed, started)
        tool, args = parsed
        level = _permission(tool, args)
        if level == PermissionLevel.APPROVAL and not approved:
            return ToolResult("denied", "This action is safety-critical and needs your approval on screen.",
                              {"reason": "approval_required"}, _elapsed(started))
        if ctx.paused and level != PermissionLevel.READ:
            return ToolResult("denied", "Device control is paused by the emergency stop. Resume it in Settings → Actions.",
                              {"reason": "paused"}, _elapsed(started))
        ctx = ctx.narrowed(tool.timeout_s)
        try:
            ctx.check()
            status, message, data = tool.handler(args, ctx)
        except ToolCancelled:
            return ToolResult("cancelled", "Stopped before it finished.", {}, _elapsed(started))
        except ToolDeadline:
            return ToolResult("timed_out", f"{name} did not finish within {tool.timeout_s:g} s.", {}, _elapsed(started))
        except Exception:  # tool failures become results; exception details are not exposed to the model/client
            return ToolResult("failed", "The local tool encountered an error.", {}, _elapsed(started))
        if status not in STATUSES:
            return ToolResult("failed", "The tool returned an unknown status.", {}, _elapsed(started))
        return ToolResult(status, message, data, _elapsed(started))

    async def run(self, name: str, arguments: dict[str, Any], ctx: ToolContext, *, approved: bool = False) -> ToolResult:
        """Run in a worker thread with a hard deadline. A handler that overruns is told to stop and reported."""
        started = time.monotonic()
        tool = self._tools.get(name)
        limit = min(ctx.remaining(), tool.timeout_s if tool else 1.0)
        task = asyncio.ensure_future(asyncio.to_thread(self.execute, name, arguments, ctx, approved=approved))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=limit + 0.5)
        except TimeoutError:
            ctx.cancel()
            return ToolResult("timed_out", f"{name} did not finish in time and was told to stop.", {}, _elapsed(started))
        except asyncio.CancelledError:
            ctx.cancel()
            raise


def _permission(tool: Tool, args: ToolInput) -> PermissionLevel:
    level = tool.permission
    if tool.classify is not None:
        custom = tool.classify(args)
        if _ORDER[custom] > _ORDER[level]:
            level = custom
    return level


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()[:5])
    return "the arguments must be an object."


def _with_time(result: ToolResult, started: float) -> ToolResult:
    return ToolResult(result.status, result.message, result.data, _elapsed(started))


def _elapsed(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
