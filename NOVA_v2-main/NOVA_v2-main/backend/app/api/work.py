from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.api.projects import project_target
from app.core.state import AppState
from app.models import Artifact, Project, User, WorkEvent, WorkItem
from app.providers.base import ChatMessage, ProviderFailure
from app.schemas import WorkEventOut, WorkItemCreate, WorkItemOut, WorkItemUpdate
from app.services import audit, suggestions
from app.services import work as work_service
from app.services.artifacts import instruction_for, label_for
from app.services.assistant import build_system_prompt
from app.workflows.engine import add_event, pick_ai

router = APIRouter(tags=["work"])


@router.get("/api/work", response_model=list[WorkItemOut])
def list_work(project_id: int | None = None, workflow_id: int | None = None,
              status: str | None = Query(default=None, max_length=24), user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> list[WorkItem]:
    stmt = select(WorkItem).where(WorkItem.owner_id == user.id)
    if project_id is not None:
        stmt = stmt.where(WorkItem.project_id == project_id)
    if workflow_id is not None:
        stmt = stmt.where(WorkItem.workflow_id == workflow_id)
    if status:
        stmt = stmt.where(WorkItem.status == status)
    return list(db.scalars(stmt.order_by(WorkItem.project_id, WorkItem.position, WorkItem.id).limit(500)))


@router.post("/api/projects/{project_id}/work", response_model=WorkItemOut, status_code=201)
def create_work(project_id: int, body: WorkItemCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
                state: AppState = Depends(get_state)) -> WorkItem:
    project = owned_project(project_id, db, user)
    item = work_service.create(db, user, project, title=body.title, description=body.description, domain=body.domain,
                               executor=body.executor, inputs=body.inputs, depends_on=body.depends_on,
                               allowlist=state.settings.allowlisted_commands)
    audit.record(db, "work.created", user_id=user.id, target=project_target(project.id),
                 details={"work_item_id": item.id, "executor": item.executor})
    return item


@router.get("/api/work/{item_id}", response_model=WorkItemOut)
def get_work(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> WorkItem:
    return work_service.get_owned(db, user, item_id)


@router.patch("/api/work/{item_id}", response_model=WorkItemOut)
def update_work(item_id: int, body: WorkItemUpdate, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> WorkItem:
    item = work_service.get_owned(db, user, item_id)
    item = work_service.update(db, item, body.model_dump(exclude_unset=True))
    audit.record(db, "work.updated", user_id=user.id, target=project_target(item.project_id),
                 details={"work_item_id": item.id, "status": item.status})
    return item


@router.delete("/api/work/{item_id}", status_code=204)
def delete_work(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    item = work_service.get_owned(db, user, item_id)
    if item.status in ("in_progress", "awaiting_approval"):
        from app.core.errors import Conflict
        raise Conflict("Cancel the task before deleting it.", code="task_active")
    for other in db.scalars(select(WorkItem).where(WorkItem.project_id == item.project_id)):
        if item.id in (other.depends_on or []):
            other.depends_on = [d for d in other.depends_on if d != item.id]
    pid = item.project_id
    db.delete(item)
    db.commit()
    audit.record(db, "work.deleted", user_id=user.id, target=project_target(pid), details={"work_item_id": item_id})
    return Response(status_code=204)


@router.post("/api/work/{item_id}/start", response_model=WorkItemOut)
def start_work(item_id: int, wait: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db),
               state: AppState = Depends(get_state)) -> WorkItem:
    item = work_service.get_owned(db, user, item_id)
    item = work_service.start(db, user, item)
    audit.record(db, "work.started", user_id=user.id, target=project_target(item.project_id),
                 details={"work_item_id": item.id, "executor": item.executor, "action_id": item.action_id})
    if item.executor == "artifact":
        user_id, wid = user.id, item.id

        async def generate(job_db: Session) -> None:
            job_item = job_db.get(WorkItem, wid)
            job_user = job_db.get(User, user_id)
            assert job_item is not None and job_user is not None
            try:
                provider, model, _ = await pick_ai(job_db, job_user, state.providers, state.settings.default_provider, "ai")
                assert provider is not None
                project = job_db.get(Project, job_item.project_id)
                kind = job_item.inputs.get("kind", "note")
                prompt = f"{instruction_for(kind)}\n\n{job_item.inputs.get('prompt', '')}"
                text = await provider.complete([ChatMessage("user", prompt)], model=model,
                                               system=build_system_prompt(job_db, job_user, project))
                if not text.strip():
                    raise ProviderFailure("The provider returned an empty document.")
                art = Artifact(project_id=job_item.project_id, kind=kind, title=f"{label_for(kind)} — {job_item.title}"[:200],
                               content=text, created_by="ai", source_input=job_item.inputs.get("prompt", ""))
                job_db.add(art)
                job_db.commit()
                job_item.status, job_item.outputs = "done", {**(job_item.outputs or {}), "artifact_id": art.id}
                job_db.commit()
                add_event(job_db, "output", f"Document created (artifact #{art.id}).", work_item_id=wid,
                          data={"artifact_id": art.id})
            except Exception as exc:  # noqa: BLE001 - every failure is recorded on the task
                job_db.rollback()
                job_item.status = "failed"
                job_item.error = getattr(exc, "message", None) or f"{type(exc).__name__}"
                job_db.commit()
                add_event(job_db, "error", job_item.error, work_item_id=wid)

        thread = state.work_jobs.start(state.db, item.id, generate)
        if wait:
            thread.join(timeout=600)
        db.refresh(item)
    return item


@router.post("/api/work/{item_id}/cancel", response_model=WorkItemOut)
def cancel_work(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                state: AppState = Depends(get_state)) -> WorkItem:
    item = work_service.get_owned(db, user, item_id)
    if item.executor == "artifact":
        state.work_jobs.cancel(item.id)
    item = work_service.cancel(db, user, item, state.runner.cancel)
    audit.record(db, "work.cancelled", user_id=user.id, target=project_target(item.project_id),
                 details={"work_item_id": item.id})
    return item


@router.get("/api/work/{item_id}/events", response_model=list[WorkEventOut])
def work_events(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[WorkEvent]:
    item = work_service.get_owned(db, user, item_id)
    return list(db.scalars(select(WorkEvent).where(WorkEvent.work_item_id == item.id).order_by(WorkEvent.id.desc()).limit(100)))


@router.get("/api/suggestions")
def get_suggestions(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db),
                    state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    if project_id is not None:
        owned_project(project_id, db, user)
    from app.models import Workflow
    running = {wf.id for wf in db.scalars(select(Workflow).where(Workflow.owner_id == user.id))
               if state.workflow_jobs.running(wf.id)}
    return suggestions.compute(db, user, state.research, project_id, running)
