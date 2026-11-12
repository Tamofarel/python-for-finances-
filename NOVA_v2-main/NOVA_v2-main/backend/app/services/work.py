"""Task engine: work items with dependencies, approvals, progress events and observed results.

Status is never advanced on a timer. It changes when the owner changes it (manual items), or when NOVA observes
a real outcome: an approval, a command's exit code, a generated artifact, or a failure.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions
from app.core.errors import AppError, Conflict, NotFound
from app.models import ActionRequest, Project, Task, User, WorkItem
from app.tools.commands import validate_argv
from app.workflows.engine import add_event

STATUSES = ("proposed", "todo", "in_progress", "awaiting_approval", "blocked", "done", "failed", "cancelled")
MANUAL_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"todo", "cancelled"},
    "todo": {"in_progress", "blocked", "done", "cancelled"},
    "in_progress": {"done", "blocked", "failed", "todo", "cancelled"},
    "blocked": {"todo", "in_progress", "cancelled"},
    "failed": {"todo", "in_progress", "cancelled"},
    "done": {"todo"},
    "cancelled": {"todo"},
    "awaiting_approval": set(),
}
EXECUTORS = ("manual", "command", "artifact")


def get_owned(db: Session, user: User, item_id: int) -> WorkItem:
    item = db.get(WorkItem, item_id)
    if item is None or item.owner_id != user.id:
        raise NotFound("Task not found.")
    return item


def _validate_deps(db: Session, item_project: int, item_id: int | None, deps: list[int]) -> list[int]:
    clean: list[int] = []
    for d in deps:
        if not isinstance(d, int) or d == item_id or d in clean:
            continue
        other = db.get(WorkItem, d)
        if other is None or other.project_id != item_project:
            raise AppError(f"Dependency #{d} is not a task in this project.", code="invalid_dependency")
        clean.append(d)
    if item_id is not None:  # cycle check: walking the new deps must never reach item_id
        stack, seen = list(clean), set()
        while stack:
            cur = stack.pop()
            if cur == item_id:
                raise AppError("This dependency would create a cycle.", code="dependency_cycle")
            if cur in seen:
                continue
            seen.add(cur)
            node = db.get(WorkItem, cur)
            if node:
                stack.extend(x for x in (node.depends_on or []) if isinstance(x, int))
    return clean


def create(db: Session, user: User, project: Project, *, title: str, description: str, domain: str, executor: str,
           inputs: dict[str, Any], depends_on: list[int], allowlist: list[list[str]]) -> WorkItem:
    if executor not in EXECUTORS:
        raise AppError("Unknown executor.", code="invalid_executor")
    requires_approval = False
    if executor == "command":
        argv = inputs.get("argv")
        if not isinstance(argv, list):
            raise AppError("A command task needs inputs.argv (a list of arguments).", code="invalid_inputs")
        inputs = {"argv": validate_argv([str(a) for a in argv], allowlist)}
        requires_approval = True  # always: commands are Category C
    elif executor == "artifact":
        from pydantic import ValidationError

        from app.schemas import ArtifactGenerateIn  # validates kind + prompt with the existing schema
        try:
            spec = ArtifactGenerateIn(kind=inputs.get("kind", "note"), input=str(inputs.get("prompt", "")))
        except ValidationError as exc:
            raise AppError("A document task needs inputs.kind (a document type) and inputs.prompt (3+ characters).",
                           code="invalid_inputs") from exc
        inputs = {"kind": spec.kind, "prompt": spec.input}
    item = WorkItem(owner_id=user.id, project_id=project.id, title=title, description=description, domain=domain,
                    executor=executor, status="todo", inputs=inputs, requires_approval=requires_approval,
                    depends_on=[], source="user")
    db.add(item)
    db.flush()
    item.depends_on = _validate_deps(db, project.id, item.id, depends_on)
    db.commit()
    add_event(db, "status", "Task created.", work_item_id=item.id)
    return item


def update(db: Session, item: WorkItem, changes: dict[str, Any]) -> WorkItem:
    if "depends_on" in changes and changes["depends_on"] is not None:
        item.depends_on = _validate_deps(db, item.project_id, item.id, list(changes["depends_on"]))
    for key in ("title", "description", "domain"):
        if changes.get(key) is not None:
            setattr(item, key, changes[key])
    new_status = changes.get("status")
    if new_status and new_status != item.status:
        if item.executor != "manual" and not (item.status in ("failed", "cancelled", "done") and new_status == "todo"):
            raise Conflict("This task's status follows its execution. Use start or cancel instead.", code="status_managed")
        if new_status not in MANUAL_TRANSITIONS.get(item.status, set()):
            raise Conflict(f"Cannot move a task from '{item.status}' to '{new_status}'.", code="invalid_transition")
        if new_status in ("in_progress", "done"):
            _require_deps_done(db, item)
        old = item.status
        item.status = new_status
        if new_status == "todo":
            item.error = None
        db.commit()
        add_event(db, "status", f"Status changed from {old} to {new_status} by you.", work_item_id=item.id)
    else:
        db.commit()
    return item


def _require_deps_done(db: Session, item: WorkItem) -> None:
    pending = []
    for d in item.depends_on or []:
        other = db.get(WorkItem, d)
        if other is not None and other.status != "done":
            pending.append(f"#{other.id} {other.title}")
    if pending:
        raise Conflict("Blocked by unfinished tasks: " + ", ".join(pending), code="blocked_by_dependencies",
                       details={"blocked_by": pending})


def start(db: Session, user: User, item: WorkItem) -> WorkItem:
    if item.status not in ("todo", "blocked", "failed"):
        raise Conflict(f"A task that is '{item.status}' cannot be started.", code="invalid_transition")
    _require_deps_done(db, item)
    if item.executor == "manual":
        item.status, item.error = "in_progress", None
        db.commit()
        add_event(db, "status", "Started by you.", work_item_id=item.id)
        return item
    if item.executor == "command":
        project = db.get(Project, item.project_id)
        assert project is not None
        ar = permissions.create_request(db, user, project, "run_command", {"argv": item.inputs["argv"], "cwd": "."},
                                        f"Task #{item.id}: {item.title}")
        item.status, item.action_id, item.error = "awaiting_approval", ar.id, None
        db.commit()
        add_event(db, "status", "Waiting for your approval to run the command.", work_item_id=item.id,
                  data={"action_id": ar.id})
        return item
    # artifact executor: the caller starts the generation job after this returns
    item.status, item.error = "in_progress", None
    db.commit()
    add_event(db, "status", "Generating the document.", work_item_id=item.id)
    return item


def cancel(db: Session, user: User, item: WorkItem, runner_cancel: Any) -> WorkItem:
    if item.status in ("done", "cancelled"):
        raise Conflict(f"Task is already {item.status}.", code="invalid_transition")
    if item.action_id:
        ar = db.get(ActionRequest, item.action_id)
        if ar is not None and ar.status == "pending":
            permissions.reject(db, ar)
        task = db.scalar(select(Task).where(Task.action_id == item.action_id).order_by(Task.id.desc()))
        if task is not None and task.status in ("queued", "running"):
            runner_cancel(task.id, starting=True)
    item.status = "cancelled"
    db.commit()
    add_event(db, "status", "Cancelled by you.", work_item_id=item.id)
    return item


# ---- hooks called by the existing approval / command pipeline -------------------------------------------
def on_action_approved(db: Session, action_id: int, task_id: int | None) -> None:
    item = db.scalar(select(WorkItem).where(WorkItem.action_id == action_id))
    if item is None:
        return
    item.status = "in_progress"
    db.commit()
    add_event(db, "status", "Approved; the command is running.", work_item_id=item.id, data={"task_id": task_id})


def on_action_rejected(db: Session, action_id: int) -> None:
    item = db.scalar(select(WorkItem).where(WorkItem.action_id == action_id))
    if item is None or item.status != "awaiting_approval":
        return
    item.status, item.action_id = "todo", None
    db.commit()
    add_event(db, "status", "You rejected the command; the task is back to 'todo'.", work_item_id=item.id)


def on_task_finished(db: Session, task: Task) -> None:
    if task.action_id is None:
        return
    item = db.scalar(select(WorkItem).where(WorkItem.action_id == task.action_id))
    if item is None or item.status == "cancelled":
        return
    result = {"task_id": task.id, "exit_code": task.exit_code, "status": task.status,
              "duration_seconds": task.duration_seconds}
    item.outputs = {**(item.outputs or {}), "last_run": result}
    if task.status == "succeeded":
        item.status, item.error = "done", None
        msg = f"Command succeeded (exit code {task.exit_code})."
    elif task.status == "cancelled":
        item.status, item.error = "cancelled", "Command cancelled."
        msg = "Command cancelled."
    else:
        item.status = "failed"
        item.error = task.error or f"Command {task.status.replace('_', ' ')} (exit code {task.exit_code})."
        msg = item.error
    db.commit()
    add_event(db, "output" if task.status == "succeeded" else "error", msg, work_item_id=item.id, data=result)


def recover_interrupted(db: Session) -> None:
    for item in db.scalars(select(WorkItem).where(WorkItem.status == "in_progress", WorkItem.executor != "manual")):
        item.status, item.error = "failed", "Interrupted: the NOVA backend stopped while this was running."
        add_event(db, "error", item.error, work_item_id=item.id, commit=False)
    db.commit()
