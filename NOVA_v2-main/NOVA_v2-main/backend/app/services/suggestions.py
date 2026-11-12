"""Next-step suggestions computed from NOVA's real state (user-initiated, read-only, no background scanning)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ActionRequest, ChangeProposal, Project, User, Workflow, WorkItem
from app.research import ResearchProvider
from app.workflows import engine


def _s(kind: str, priority: int, title: str, detail: str, target: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "priority": priority, "title": title, "detail": detail, "target": target}


def compute(db: Session, user: User, research: ResearchProvider, project_id: int | None = None,
            running_workflows: set[int] | None = None) -> list[dict[str, Any]]:
    running_workflows = running_workflows or set()
    out: list[dict[str, Any]] = []
    proj_filter = (lambda col: col == project_id) if project_id is not None else (lambda col: col.is_not(None))

    pending_actions = db.scalar(select(func.count(ActionRequest.id)).where(
        ActionRequest.owner_id == user.id, ActionRequest.status == "pending", proj_filter(ActionRequest.project_id))) or 0
    if pending_actions:
        plural = "s" if pending_actions > 1 else ""
        out.append(_s("approval", 100, f"Review {pending_actions} action{plural} waiting for approval",
                      "Commands and deletions wait for your explicit confirmation.", {"type": "approvals"}))

    open_states = ["active", "awaiting_decision"]
    for wf in db.scalars(select(Workflow).where(Workflow.owner_id == user.id, Workflow.status.in_(open_states),
                                                proj_filter(Workflow.project_id)).order_by(Workflow.updated_at.desc())):
        target = {"type": "workflow", "id": wf.id, "project_id": wf.project_id}
        if wf.id in running_workflows:
            continue  # work is in progress; nothing to suggest
        if wf.status == "awaiting_decision":
            out.append(_s("decision", 90, f"Review the proposal for “{wf.title}”",
                          "Approve it, ask for a revision, or reject it.", target))
            continue
        questions = engine.questions_of(db, wf)
        open_q = engine.open_question(questions)
        clarify = engine.stage(wf, "clarify")
        if clarify["status"] != "done":
            if open_q:
                out.append(_s("question", 80, f"Answer: {open_q.question}", f"Workflow “{wf.title}”.", target))
            elif clarify.get("ready"):
                out.append(_s("continue", 70, f"Finish clarifying “{wf.title}”",
                              "The key questions are covered; continue to research.", target))
            else:
                out.append(_s("question", 60, f"Get the next question for “{wf.title}”", "", target))
            continue
        failed = [s for s in engine.STAGES if engine.stage(wf, s)["status"] in ("failed", "interrupted", "cancelled")]
        if failed:
            st = engine.stage(wf, failed[0])
            out.append(_s("recover", 85, f"Retry “{engine.STAGE_LABELS[failed[0]]}” for “{wf.title}”",
                          st.get("error") or "This stage stopped before finishing.", target))
            continue
        nxt = engine.next_pending_stage(wf)
        if nxt:
            out.append(_s("continue", 65, f"Run “{engine.STAGE_LABELS[nxt]}” for “{wf.title}”", "", target))
        pending_research = [f for f in engine.findings_of(db, wf) if f.status == "pending"]
        if pending_research and not research.available():
            out.append(_s("configure", 30, f"{len(pending_research)} research questions are pending",
                          "Configure a search provider (SearXNG) to research them, or investigate them yourself.", target))

    items = list(db.scalars(select(WorkItem).where(WorkItem.owner_id == user.id, proj_filter(WorkItem.project_id))))
    by_id = {i.id: i for i in items}
    for item in items:
        target = {"type": "work", "id": item.id, "project_id": item.project_id}
        if item.status == "failed":
            out.append(_s("recover", 75, f"Task failed: {item.title}", item.error or "", target))
        elif item.status == "todo":
            deps = [by_id.get(d) for d in item.depends_on or []]
            if all(d is None or d.status == "done" for d in deps):
                out.append(_s("start", 50 - min(item.position, 20), f"Ready to start: {item.title}",
                              "All dependencies are done.", target))
        elif item.status == "blocked":
            out.append(_s("decision", 55, f"Blocked: {item.title}", "Decide how to unblock it, or cancel it.", target))

    changes = db.execute(select(ChangeProposal.project_id, func.count(ChangeProposal.id))
                         .join(Project, Project.id == ChangeProposal.project_id)
                         .where(Project.owner_id == user.id, ChangeProposal.status == "pending",
                                proj_filter(ChangeProposal.project_id)).group_by(ChangeProposal.project_id)).all()
    for pid, n in changes:
        out.append(_s("review", 70, f"Review {n} pending file change{'s' if n > 1 else ''}", "",
                      {"type": "changes", "project_id": pid}))
    out.sort(key=lambda s: -s["priority"])
    return out[:12]
