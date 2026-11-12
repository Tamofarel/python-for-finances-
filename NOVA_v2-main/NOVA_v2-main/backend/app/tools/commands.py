"""Restricted command execution.

- Commands are argument arrays; no shell is ever used.
- The argv must start with an allowlisted prefix (e.g. ["python", "-m", "pytest"]).
- Arguments may not contain '..' segments, absolute paths, or shell metacharacters.
- The working directory is always the project workspace.
- The child environment is minimal: provider keys and NOVA_* settings are never passed.
- Hard timeout and output size cap; processes can be cancelled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import AppError, Forbidden

_FORBIDDEN_CHARS = set("|&;<>`$\n\r\x00")
_ENV_PASSTHROUGH = ["PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
                    "HOME", "USERPROFILE", "LANG", "LC_ALL", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"]


class CommandRejected(Forbidden):
    code = "command_rejected"


def validate_argv(argv: list[str], allowlist: list[list[str]]) -> list[str]:
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise CommandRejected("Command must be a non-empty list of non-empty strings.")
    if len(argv) > 64 or sum(len(a) for a in argv) > 4000:
        raise CommandRejected("Command is too long.")
    for a in argv:
        if set(a) & _FORBIDDEN_CHARS:
            raise CommandRejected(f"Argument contains forbidden characters: {a!r}")
        norm = a.replace("\\", "/")
        if ".." in norm.split("/") or "=.." in norm:
            raise CommandRejected("Arguments may not reference parent directories ('..').")
        if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":") or norm.startswith("~"):
            raise CommandRejected("Arguments may not contain absolute paths.")
    for prefix in allowlist:
        if argv[: len(prefix)] == prefix:
            return argv
    allowed = ", ".join(" ".join(p) for p in allowlist)
    raise CommandRejected(f"Command is not allowlisted. Allowed prefixes: {allowed}")


def resolve_executable(name: str) -> str:
    if name in ("python", "python3"):
        return sys.executable  # the interpreter running NOVA
    found = shutil.which(name)
    if not found:
        raise AppError(f"Executable '{name}' was not found on PATH.", code="executable_not_found")
    return found


def safe_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_PASSTHROUGH}
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return env


@dataclass
class RunResult:
    status: str  # succeeded | failed | timed_out | cancelled | error
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    error: str | None = None


@dataclass
class _Running:
    proc: subprocess.Popen[bytes]
    cancelled: threading.Event = field(default_factory=threading.Event)


class CommandRunner:
    def __init__(self, timeout: int, max_output: int) -> None:
        self.timeout = timeout
        self.max_output = max_output
        self._running: dict[int, _Running] = {}
        self._cancel_requested: set[int] = set()  # cancelled before the process was registered
        self._closed = False  # set at shutdown: nothing new may start
        self._lock = threading.Lock()

    def _clip(self, data: bytes) -> str:
        text = data.decode("utf-8", errors="replace")
        if len(data) > self.max_output:
            return text[: self.max_output] + f"\n…[output truncated at {self.max_output} bytes]"
        return text

    def run(self, task_id: int, argv: list[str], cwd: Path, timeout: int | None = None) -> RunResult:
        start = time.monotonic()
        exe = resolve_executable(argv[0])
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        with self._lock:
            if self._closed or task_id in self._cancel_requested:
                self._cancel_requested.discard(task_id)
                return RunResult("cancelled", None, "", "", 0.0, "Cancelled before the command started.")
        try:
            proc = subprocess.Popen([exe, *argv[1:]], cwd=str(cwd), env=safe_env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, **kwargs)
        except OSError as exc:
            return RunResult("error", None, "", "", time.monotonic() - start, f"Failed to start: {exc}")
        running = _Running(proc)
        with self._lock:
            self._running[task_id] = running
            # A cancellation or shutdown that happened while the process was starting still applies.
            stop_now = self._closed or task_id in self._cancel_requested
            self._cancel_requested.discard(task_id)
        if stop_now:
            running.cancelled.set()
            proc.kill()
        try:
            out, err = proc.communicate(timeout=timeout or self.timeout)
            status = "succeeded" if proc.returncode == 0 else "failed"
            if running.cancelled.is_set():
                status = "cancelled"
            return RunResult(status, proc.returncode, self._clip(out), self._clip(err), time.monotonic() - start)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return RunResult("timed_out", None, self._clip(out), self._clip(err), time.monotonic() - start,
                             f"Timed out after {timeout or self.timeout}s")
        finally:
            with self._lock:
                self._running.pop(task_id, None)

    def cancel_all(self) -> int:
        """Shutdown: stop every running command and refuse to start new ones."""
        with self._lock:
            self._closed = True
            ids = list(self._running)
        return sum(1 for i in ids if self.cancel(i))

    def cancel(self, task_id: int, starting: bool = False) -> bool:
        """Stop a running command. With starting=True, a command that is about to start is cancelled too."""
        with self._lock:
            running = self._running.get(task_id)
            if not running:
                if starting:
                    self._cancel_requested.add(task_id)
                return starting
        running.cancelled.set()
        running.proc.kill()
        return True
