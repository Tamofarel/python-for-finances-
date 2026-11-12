"""Background task execution (in-process threads; no distributed queue for a local-first app)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.db.session import Database
from app.models import Task
from app.services import audit
from app.tools.commands import CommandRunner

log = get_logger("nova.tasks")


def start_command_task(db_handle: Database, runner: CommandRunner, task_id: int, argv: list[str], cwd: Path,
                       user_id: int, wait: bool = False) -> threading.Thread:
    def _work() -> None:
        with db_handle.SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            task.status = "running"
            task.started_at = datetime.now(UTC)
            db.commit()
            try:
                res = runner.run(task_id, argv, cwd)
                task.status = res.status if res.status != "error" else "failed"
                task.exit_code = res.exit_code
                task.stdout, task.stderr = res.stdout, res.stderr
                task.error = res.error
                task.duration_seconds = round(res.duration, 3)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as a task error
                log.exception("Command task failed")
                task.status = "failed"
                task.error = getattr(exc, "message", None) or f"{type(exc).__name__}"
            task.finished_at = datetime.now(UTC)
            db.commit()
            from app.services.work import on_task_finished  # local import avoids an import cycle
            on_task_finished(db, task)
            audit.record(db, "command.finished", user_id=user_id, target=" ".join(argv)[:255],
                         outcome="success" if task.status == "succeeded" else "failure",
                         details={"task_id": task.id, "status": task.status, "exit_code": task.exit_code,
                                  "duration_seconds": task.duration_seconds})

    t = threading.Thread(target=_work, name=f"nova-task-{task_id}", daemon=True)
    t.start()
    if wait:
        t.join()
    return t
