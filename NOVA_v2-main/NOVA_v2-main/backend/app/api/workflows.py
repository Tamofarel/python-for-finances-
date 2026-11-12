from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.api.projects import project_target
from app.core.errors import AppError, Conflict
from app.core.state import AppState
from app.models import Project, User, Workflow
from app.schemas import WorkflowAnswer, WorkflowCreate, WorkflowDecision, WorkflowMode
from app.services import audit
from app.workflows import engine
from app.workflows.templates import TEMPLATES, suggest_template

router = APIRouter(tags=["workflows"])


def _detail(db: Session, state: AppState, wf: Workflow) -> dict[str, Any]:
    db.refresh(wf)
    return engine.detail(db, wf, state.workflow_jobs.running(wf.id))


@router.get("/api/workflow-templates")
def list_templates(user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [{"id": t.id, "label": t.label, "description": t.description,
             "topics": [{"id": x.id, "question": x.question, "required": x.required} for x in t.topics],
             "research_questions": [r.question for r in t.research]} for t in TEMPLATES.values()]


@router.get("/api/workflow-templates/suggest")
def suggest(idea: str = Query(min_length=3, max_length=10_000), user: User = Depends(current_user)) -> dict[str, str]:
    template, reason = suggest_template(idea)
    return {"template": template, "label": TEMPLATES[template].label, "reason": reason}


@router.get("/api/workflows")
def list_workflows(project_id: int | None = None, status: str | None = Query(default=None, max_length=24),
                   user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    stmt = select(Workflow).where(Workflow.owner_id == user.id)
    if project_id is not None:
        stmt = stmt.where(Workflow.project_id == project_id)
    if status:
        stmt = stmt.where(Workflow.status == status)
    out = []
    for wf in db.scalars(stmt.order_by(Workflow.updated_at.desc()).limit(100)):
        q = engine.open_question(engine.questions_of(db, wf))
        out.append({"id": wf.id, "project_id": wf.project_id, "title": wf.title, "status": wf.status,
                    "template": wf.template, "current_stage": wf.current_stage,
                    "current_stage_label": engine.STAGE_LABELS.get(wf.current_stage, wf.current_stage),
                    "running": state.workflow_jobs.running(wf.id),
                    "open_question": {"id": q.id, "question": q.question} if q else None,
                    "updated_at": engine.iso(wf.updated_at)})
    return out


@router.post("/api/workflows", status_code=201)
async def create_workflow(body: WorkflowCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
                          state: AppState = Depends(get_state)) -> dict[str, Any]:
    template = body.template
    if template == "auto":
        template = suggest_template(body.idea)[0]
    if template not in TEMPLATES:
        raise AppError("Unknown template.", code="invalid_template")
    auto_project = body.project_id is None
    if body.project_id is not None:
        project = owned_project(body.project_id, db, user)
    else:  # create a project for the idea, with a confined default workspace
        from app.api.projects import create_project
        from app.schemas import ProjectCreate
        title = (body.title or engine.short_title(body.idea))[:120]
        project = create_project(ProjectCreate(name=title, description=body.idea[:5000]), user=user, db=db, state=state)
    wf = engine.create(db, user, project, body.idea, template, body.title)
    if auto_project:
        engine.set_stage(wf, "capture", auto_project=True)
        db.commit()
    audit.record(db, "workflow.created", user_id=user.id, target=project_target(project.id),
                 details={"workflow_id": wf.id, "template": wf.template})
    await engine.ask_next(db, user, wf, state.providers, state.settings.default_provider)
    return _detail(db, state, wf)


@router.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                 state: AppState = Depends(get_state)) -> dict[str, Any]:
    return _detail(db, state, engine.get_owned(db, user, workflow_id))


def _active(wf: Workflow) -> None:
    if wf.status not in ("active", "awaiting_decision"):
        raise Conflict(f"This workflow is {wf.status}. Ask for a revision to reopen it.", code="workflow_closed")


@router.post("/api/workflows/{workflow_id}/questions/{question_id}/answer")
async def answer_question(workflow_id: int, question_id: int, body: WorkflowAnswer, mode: str = "auto",
                          user: User = Depends(current_user), db: Session = Depends(get_db),
                          state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    _active(wf)
    engine.answer(db, wf, question_id, body.answer)
    await engine.ask_next(db, user, wf, state.providers, state.settings.default_provider, _mode(mode))
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/questions/{question_id}/skip")
async def skip_question(workflow_id: int, question_id: int, mode: str = "auto", user: User = Depends(current_user),
                        db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    _active(wf)
    engine.answer(db, wf, question_id, None)
    await engine.ask_next(db, user, wf, state.providers, state.settings.default_provider, _mode(mode))
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/questions/next")
async def next_question(workflow_id: int, body: WorkflowMode, user: User = Depends(current_user),
                        db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    _active(wf)
    if engine.stage(wf, "clarify")["status"] == "done":
        raise Conflict("Clarification is finished. Ask for a revision from 'clarify' to add questions.", code="clarify_done")
    await engine.ask_next(db, user, wf, state.providers, state.settings.default_provider, body.mode)
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/clarify/complete")
def complete_clarify(workflow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                     state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    _active(wf)
    engine.complete_clarify(db, wf)
    return _detail(db, state, wf)


def _mode(mode: str) -> str:
    if mode not in ("auto", "ai", "template"):
        raise AppError("mode must be auto, ai or template.", code="invalid_mode")
    return mode


def _start(db: Session, state: AppState, user: User, wf: Workflow, stage_name: str, mode: str, wait: bool,
           run_all: bool = False) -> dict[str, Any]:
    _active(wf)
    if engine.stage(wf, "clarify")["status"] != "done":
        raise Conflict("Finish clarifying the idea first.", code="clarify_pending")
    if stage_name not in engine.RUNNABLE_STAGES:
        raise AppError(f"Stage '{stage_name}' cannot be run.", code="invalid_stage")
    if state.workflow_jobs.running(wf.id):
        raise Conflict("A stage is already running for this workflow.", code="job_running")
    if wf.status == "awaiting_decision" and stage_name != "proposal":
        wf.status = "active"  # the proposal is outdated once an earlier stage is rerun
        engine.set_stage(wf, "decision", status="pending")
        db.commit()
    wf_id, user_id = wf.id, user.id

    async def work(job_db: Session) -> None:
        job_user = job_db.get(User, user_id)
        job_wf = job_db.get(Workflow, wf_id)
        assert job_user is not None and job_wf is not None
        current: str | None = stage_name
        while current is not None:
            await engine.run_stage(job_db, job_user, job_wf, current, registry=state.providers,
                                   default_provider=state.settings.default_provider, research=state.research, mode=mode)
            job_db.refresh(job_wf)
            if not run_all or engine.stage(job_wf, current)["status"] not in ("done", "skipped"):
                break
            current = engine.next_pending_stage(job_wf)

    thread = state.workflow_jobs.start(state.db, wf.id, work)
    audit.record(db, "workflow.stage_started", user_id=user.id, target=project_target(wf.project_id),
                 details={"workflow_id": wf.id, "stage": stage_name, "mode": mode, "run_all": run_all})
    if wait:
        thread.join(timeout=900)
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/stages/{stage_name}/run")
def run_stage(workflow_id: int, stage_name: str, body: WorkflowMode, wait: bool = False,
              user: User = Depends(current_user), db: Session = Depends(get_db),
              state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    return _start(db, state, user, wf, stage_name, body.mode, wait)


@router.post("/api/workflows/{workflow_id}/continue")
def continue_workflow(workflow_id: int, body: WorkflowMode, wait: bool = False, all: bool = False,  # noqa: A002
                      user: User = Depends(current_user), db: Session = Depends(get_db),
                      state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Run the next pending stage, or (all=true) every remaining stage up to the proposal, one after another."""
    wf = engine.get_owned(db, user, workflow_id)
    nxt = engine.next_pending_stage(wf)
    if nxt is None:
        raise Conflict("Every stage is complete. Review the proposal.", code="nothing_to_run")
    return _start(db, state, user, wf, nxt, body.mode, wait, run_all=all)


@router.post("/api/workflows/{workflow_id}/stages/{stage_name}/skip")
def skip_stage(workflow_id: int, stage_name: str, user: User = Depends(current_user), db: Session = Depends(get_db),
               state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    _active(wf)
    if state.workflow_jobs.running(wf.id):
        raise Conflict("Wait for the running stage to finish or cancel it.", code="job_running")
    engine.skip_stage(db, wf, stage_name)
    audit.record(db, "workflow.stage_skipped", user_id=user.id, target=project_target(wf.project_id),
                 details={"workflow_id": wf.id, "stage": stage_name})
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/dismiss")
def dismiss(workflow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
            state: AppState = Depends(get_state)) -> dict[str, Any]:
    """'This was not an idea': archive the workflow and the project NOVA created for it (both reversible)."""
    wf = engine.get_owned(db, user, workflow_id)
    if state.workflow_jobs.running(wf.id):
        raise Conflict("Cancel the running stage first.", code="job_running")
    wf.status = "archived"
    project = db.get(Project, wf.project_id)
    if project is not None and engine.stage(wf, "capture").get("auto_project"):
        project.archived = True
    db.commit()
    audit.record(db, "workflow.dismissed", user_id=user.id, target=project_target(wf.project_id),
                 details={"workflow_id": wf.id})
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/cancel")
def cancel_job(workflow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
               state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    if not state.workflow_jobs.cancel(wf.id):
        raise Conflict("Nothing is running for this workflow.", code="not_running")
    audit.record(db, "workflow.cancelled", user_id=user.id, target=project_target(wf.project_id),
                 details={"workflow_id": wf.id})
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/decision")
async def decide(workflow_id: int, body: WorkflowDecision, user: User = Depends(current_user),
                 db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    if state.workflow_jobs.running(wf.id):
        raise Conflict("Wait for the running stage to finish or cancel it.", code="job_running")
    try:
        items = await engine.decide(db, user, wf, body.decision, proposal_sha256=body.proposal_sha256,
                                    feedback=body.feedback, reopen_stage=body.reopen_stage, registry=state.providers,
                                    default_provider=state.settings.default_provider, mode=body.mode)
    except AppError as exc:
        audit.record(db, f"workflow.{body.decision}", user_id=user.id, target=project_target(wf.project_id),
                     outcome="denied", details={"workflow_id": wf.id, "reason": exc.code})
        raise
    audit.record(db, f"workflow.{body.decision}", user_id=user.id, target=project_target(wf.project_id),
                 details={"workflow_id": wf.id, "tasks_created": len(items)})
    return _detail(db, state, wf)


@router.post("/api/workflows/{workflow_id}/archive")
def archive(workflow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
            state: AppState = Depends(get_state)) -> dict[str, Any]:
    wf = engine.get_owned(db, user, workflow_id)
    if state.workflow_jobs.running(wf.id):
        raise Conflict("Cancel the running stage first.", code="job_running")
    wf.status = "archived"
    db.commit()
    return _detail(db, state, wf)

