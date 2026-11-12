from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions
from app.api.deps import current_user, get_db, get_state, owned_project
from app.api.projects import project_target
from app.core.errors import AppError, NotFound
from app.core.state import AppState
from app.models import ActionRequest, Project, Task, User
from app.schemas import ActionOut, ApproveIn, ApproveOut, CommandRequestIn, DeleteRequestIn, MoveRequestIn, TaskOut
from app.services import audit, changes
from app.services import work as work_service
from app.services.agent import PAUSED_KEY, is_paused
from app.services.assistant import set_setting
from app.tasks import start_command_task
from app.tools.commands import validate_argv
from app.tools.registry import ToolContext
from app.workspaces import normalize_rel

router = APIRouter(tags=["actions"])


@router.post("/api/projects/{project_id}/actions/command", response_model=ActionOut, status_code=201)
def request_command(project_id: int, body: CommandRequestIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ActionRequest:
    proj = owned_project(project_id, db, user)
    try:
        argv = validate_argv(body.argv, state.settings.allowlisted_commands)
    except AppError as exc:
        audit.record(db, "command.requested", user_id=user.id, target=project_target(proj.id),
                     outcome="denied", details={"argv": body.argv[:16], "reason": exc.message})
        raise
    ar = permissions.create_request(db, user, proj, "run_command", {"argv": argv, "cwd": "."}, body.reason)
    audit.record(db, "command.requested", user_id=user.id, target=project_target(proj.id),
                 details={"action_id": ar.id, "argv": argv})
    return ar


@router.post("/api/projects/{project_id}/actions/delete", response_model=ActionOut, status_code=201)
def request_delete(project_id: int, body: DeleteRequestIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ActionRequest:
    proj = owned_project(project_id, db, user)
    ws = changes.workspace_for(proj, state.settings.max_file_bytes)
    clean, target = ws.resolve(body.path)
    if not clean or not target.exists():
        raise NotFound("Path not found.")
    ar = permissions.create_request(db, user, proj, "delete_path", {"path": normalize_rel(clean)}, body.reason)
    audit.record(db, "delete.requested", user_id=user.id, target=project_target(proj.id, clean),
                 details={"action_id": ar.id})
    return ar


@router.post("/api/projects/{project_id}/actions/move", response_model=ActionOut, status_code=201)
def request_move(project_id: int, body: MoveRequestIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ActionRequest:
    proj = owned_project(project_id, db, user)
    ws = changes.workspace_for(proj, state.settings.max_file_bytes)
    src, s_path = ws.resolve(body.src)
    dst, d_path = ws.resolve(body.dst)
    if not src or not s_path.exists():
        raise NotFound("Source path not found.")
    if not dst or d_path.exists():
        raise AppError("The destination already exists; choose another name.", code="exists", status_code=409)
    ar = permissions.create_request(db, user, proj, "move_path", {"src": src, "dst": dst}, body.reason)
    audit.record(db, "move.requested", user_id=user.id, target=project_target(proj.id, src),
                 details={"action_id": ar.id, "dst": dst})
    return ar


@router.get("/api/actions", response_model=list[ActionOut])
def list_actions(status: str | None = Query(default=None, max_length=16), project_id: int | None = None,
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ActionRequest]:
    stmt = select(ActionRequest).where(ActionRequest.owner_id == user.id)
    if status:
        stmt = stmt.where(ActionRequest.status == status)
    if project_id is not None:
        stmt = stmt.where(ActionRequest.project_id == project_id)
    return list(db.scalars(stmt.order_by(ActionRequest.id.desc()).limit(200)))


@router.get("/api/actions/control")
def action_control(user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"paused": is_paused(db, user), "running": state.action_runs.count(user.id)}


@router.post("/api/actions/emergency-stop")
def emergency_stop(user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Stop everything NOVA is doing on this computer and on devices, and refuse control until resumed."""
    set_setting(db, user, PAUSED_KEY, True)
    cancelled = state.action_runs.cancel_all(user.id)
    commands = 0
    for task in db.scalars(select(Task).where(Task.owner_id == user.id, Task.status.in_(["queued", "running"]))):
        commands += int(state.runner.cancel(task.id, starting=True))
    rejected = 0
    for ar in db.scalars(select(ActionRequest).where(ActionRequest.owner_id == user.id,
                                                     ActionRequest.status == "pending", ActionRequest.kind == "tool_call")):
        permissions.reject(db, ar)
        rejected += 1
    audit.record(db, "actions.emergency_stop", user_id=user.id, target="actions",
                 details={"cancelled_actions": cancelled, "cancelled_commands": commands, "rejected_requests": rejected})
    return {"paused": True, "cancelled_actions": cancelled, "cancelled_commands": commands, "rejected_requests": rejected}


@router.post("/api/actions/resume")
def resume_actions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    set_setting(db, user, PAUSED_KEY, False)
    audit.record(db, "actions.resumed", user_id=user.id, target="actions")
    return {"paused": False}


def _approve_tool_call(ar: ActionRequest, body: ApproveIn, user: User, db: Session, state: AppState) -> ApproveOut:
    """Run a queued safety-critical assistant action exactly as requested, once, after on-screen approval."""
    try:
        permissions.consume_approval(db, ar, body.params_hash)
    except AppError as exc:
        audit.record(db, "tool_call.approved", user_id=user.id, target="actions", outcome="denied",
                     details={"action_id": ar.id, "reason": exc.code})
        raise
    name, arguments = str(ar.params.get("tool", "")), dict(ar.params.get("arguments") or {})
    ctx = ToolContext(user.id, time.monotonic() + 60, is_paused(db, user))
    state.action_runs.add(user.id, ctx)
    try:
        result = state.tools.execute(name, arguments, ctx, approved=True)
    finally:
        state.action_runs.remove(user.id, ctx)
    audit.record(db, "tool_call.approved", user_id=user.id, target="actions", outcome=result.status,
                 details={"action_id": ar.id, "tool": name, "duration_ms": result.duration_ms,
                          "verified": bool(result.data.get("verified", False))})
    return ApproveOut(action=ActionOut.model_validate(ar),
                      result={"status": result.status, "message": result.message, "data": result.data})


@router.post("/api/actions/{action_id}/approve", response_model=ApproveOut)
def approve_action(action_id: int, body: ApproveIn, wait: bool = False, user: User = Depends(current_user),
                   db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ApproveOut:
    ar = permissions.get_request(db, user, action_id)
    if ar.kind == "tool_call":
        return _approve_tool_call(ar, body, user, db, state)
    proj = db.get(Project, ar.project_id) if ar.project_id is not None else None
    if proj is None or proj.owner_id != user.id:
        raise NotFound("Project not found.")
    target = project_target(proj.id, ar.kind)
    try:
        permissions.consume_approval(db, ar, body.params_hash)
    except AppError as exc:
        audit.record(db, f"{ar.kind}.approved", user_id=user.id, target=target, outcome="denied",
                     details={"action_id": ar.id, "reason": exc.code})
        if exc.code == "expired":
            work_service.on_action_rejected(db, ar.id)
        raise
    audit.record(db, f"{ar.kind}.approved", user_id=user.id, target=target, details={"action_id": ar.id})
    ws = changes.workspace_for(proj, state.settings.max_file_bytes)

    if ar.kind == "run_command":
        argv = validate_argv(list(ar.params["argv"]), state.settings.allowlisted_commands)  # re-check
        task = Task(owner_id=user.id, project_id=proj.id, action_id=ar.id, kind="command", command=argv)
        db.add(task)
        db.commit()
        work_service.on_action_approved(db, ar.id, task.id)
        thread = start_command_task(state.db, state.runner, task.id, argv, ws.root, user.id)
        if wait:
            thread.join(timeout=state.settings.command_timeout_seconds + 10)
        db.refresh(task)
        return ApproveOut(action=ActionOut.model_validate(ar), task=TaskOut.model_validate(task))

    if ar.kind == "delete_path":
        ws.delete(ar.params["path"])
        audit.record(db, "file.deleted", user_id=user.id, target=project_target(proj.id, ar.params["path"]),
                     details={"action_id": ar.id})
        return ApproveOut(action=ActionOut.model_validate(ar), result={"deleted": ar.params["path"]})

    if ar.kind == "move_path":
        src, dst = ws.move(ar.params["src"], ar.params["dst"])
        audit.record(db, "file.moved", user_id=user.id, target=project_target(proj.id, src),
                     details={"action_id": ar.id, "dst": dst})
        return ApproveOut(action=ActionOut.model_validate(ar), result={"moved": src, "to": dst})

    raise AppError(f"Unsupported action kind '{ar.kind}'.", code="unsupported_action")


@router.post("/api/actions/{action_id}/reject", response_model=ActionOut)
def reject_action(action_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ActionRequest:
    ar = permissions.get_request(db, user, action_id)
    permissions.reject(db, ar)
    work_service.on_action_rejected(db, ar.id)
    target = project_target(ar.project_id, ar.kind) if ar.project_id is not None else "actions"
    audit.record(db, f"{ar.kind}.rejected", user_id=user.id, target=target, details={"action_id": ar.id})
    return ar


@router.get("/api/tasks", response_model=list[TaskOut])
def list_tasks(project_id: int | None = None, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> list[Task]:
    stmt = select(Task).where(Task.owner_id == user.id)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    return list(db.scalars(stmt.order_by(Task.id.desc()).limit(100)))


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise NotFound("Task not found.")
    return task


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                state: AppState = Depends(get_state)) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise NotFound("Task not found.")
    if task.status not in ("queued", "running"):
        raise AppError(f"Task is already {task.status}.", code="not_running", status_code=409)
    cancelled = state.runner.cancel(task.id, starting=True)
    audit.record(db, "command.cancel", user_id=user.id, target=project_target(task.project_id or 0, "task"),
                 outcome="success" if cancelled else "failure", details={"task_id": task.id})
    db.refresh(task)
    return task
