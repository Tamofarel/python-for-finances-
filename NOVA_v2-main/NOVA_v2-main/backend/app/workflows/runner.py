"""Runs workflow stages in background threads (each with its own event loop and DB session).

A stage can take minutes on a local model, so the HTTP request only starts it; the UI follows progress through
the persisted stage state and events. One job per workflow at a time; jobs can be cancelled.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import Database

log = get_logger("nova.workflow.runner")


@dataclass
class _Job:
    thread: threading.Thread
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[None] | None = None
    ready: threading.Event = field(default_factory=threading.Event)


class WorkflowJobs:
    def __init__(self) -> None:
        self._jobs: dict[int, _Job] = {}
        self._lock = threading.Lock()

    def running(self, workflow_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(workflow_id)
        return bool(job and job.thread.is_alive())

    def start(self, db_handle: Database, workflow_id: int, work: Callable[[Session], Awaitable[None]]) -> threading.Thread:
        with self._lock:
            existing = self._jobs.get(workflow_id)
            if existing and existing.thread.is_alive():
                raise RuntimeError("already running")

        def target() -> None:
            loop = asyncio.new_event_loop()
            job.loop = loop
            try:
                with db_handle.SessionLocal() as db:
                    job.task = loop.create_task(work(db))
                    job.ready.set()
                    try:
                        loop.run_until_complete(job.task)
                    except asyncio.CancelledError:
                        pass
                    except Exception:  # noqa: BLE001 - recorded on the stage by the engine; log anything else
                        log.exception("Workflow job crashed")
            finally:
                job.ready.set()
                loop.close()
                with self._lock:
                    if self._jobs.get(workflow_id) is job:
                        del self._jobs[workflow_id]

        job = _Job(thread=threading.Thread(target=target, name=f"nova-workflow-{workflow_id}", daemon=True))
        with self._lock:
            self._jobs[workflow_id] = job
        job.thread.start()
        return job.thread

    def cancel(self, workflow_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(workflow_id)
        if not job or not job.thread.is_alive():
            return False
        job.ready.wait(timeout=5)
        if job.loop and job.task and not job.task.done():
            job.loop.call_soon_threadsafe(job.task.cancel)
            return True
        return False

    def cancel_all(self) -> int:
        with self._lock:
            ids = list(self._jobs)
        return sum(1 for i in ids if self.cancel(i))
