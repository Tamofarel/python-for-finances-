"""Memory, audit history, research and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.core.errors import AppError, NotFound
from app.core.state import AppState
from app.models import Artifact, AuditEvent, MemoryRecord, User
from app.providers.base import ChatMessage, ProviderFailure
from app.research import ResearchUnavailable
from app.schemas import AuditOut, MemoryCreate, MemoryOut, MemoryUpdate, ResearchIn
from app.services import audit
from app.services.artifacts import instruction_for
from app.services.assistant import build_system_prompt, resolve_provider, untrusted

router = APIRouter(tags=["misc"])


# ---- health -------------------------------------------------------------------------------
@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "nova"}


@router.get("/api/ready")
def ready(db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> dict[str, object]:
    db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok", "research": state.research.name,
            "providers": state.providers.names()}


# ---- memory ---------------------------------------------------------------------------------
def _memory(db: Session, user: User, memory_id: int) -> MemoryRecord:
    rec = db.get(MemoryRecord, memory_id)
    if rec is None or rec.owner_id != user.id:
        raise NotFound("Memory not found.")
    return rec


@router.get("/api/memory", response_model=list[MemoryOut])
def list_memory(q: str | None = Query(default=None, max_length=200), project_id: int | None = None,
                scope: str = Query(default="all", pattern="^(all|general|project)$"),
                user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[MemoryRecord]:
    stmt = select(MemoryRecord).where(MemoryRecord.owner_id == user.id)
    if scope == "general":
        stmt = stmt.where(MemoryRecord.project_id.is_(None))
    elif scope == "project" or project_id is not None:
        stmt = stmt.where(MemoryRecord.project_id == project_id) if project_id is not None else \
            stmt.where(MemoryRecord.project_id.is_not(None))
    if q:
        stmt = stmt.where(MemoryRecord.content.ilike(f"%{q}%"))
    return list(db.scalars(stmt.order_by(MemoryRecord.updated_at.desc())))


@router.post("/api/memory", response_model=MemoryOut, status_code=201)
def create_memory(body: MemoryCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> MemoryRecord:
    if body.project_id is not None:
        owned_project(body.project_id, db, user)
    rec = MemoryRecord(owner_id=user.id, project_id=body.project_id, kind=body.kind, content=body.content.strip(),
                       source="user")
    db.add(rec)
    db.commit()
    audit.record(db, "memory.created", user_id=user.id, target=f"memory:{rec.id}", details={"kind": rec.kind})
    return rec


@router.patch("/api/memory/{memory_id}", response_model=MemoryOut)
def update_memory(memory_id: int, body: MemoryUpdate, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> MemoryRecord:
    rec = _memory(db, user, memory_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(rec, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    audit.record(db, "memory.updated", user_id=user.id, target=f"memory:{rec.id}")
    return rec


@router.delete("/api/memory/{memory_id}", status_code=204)
def delete_memory(memory_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    rec = _memory(db, user, memory_id)
    db.delete(rec)
    db.commit()
    audit.record(db, "memory.deleted", user_id=user.id, target=f"memory:{memory_id}")
    return Response(status_code=204)


@router.delete("/api/memory", status_code=204)
def clear_memory(scope: str = Query(pattern="^(general|all)$"), confirm: bool = False,
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    if not confirm:
        raise AppError("Pass confirm=true to clear memory.", code="confirmation_required")
    stmt = select(MemoryRecord).where(MemoryRecord.owner_id == user.id)
    if scope == "general":
        stmt = stmt.where(MemoryRecord.project_id.is_(None))
    n = 0
    for rec in db.scalars(stmt):
        db.delete(rec)
        n += 1
    db.commit()
    audit.record(db, "memory.cleared", user_id=user.id, target=f"memory:{scope}", details={"count": n})
    return Response(status_code=204)


# ---- audit ---------------------------------------------------------------------------------
@router.get("/api/audit", response_model=list[AuditOut])
def audit_history(action: str | None = Query(default=None, max_length=64), limit: int = Query(default=100, le=500),
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[AuditEvent]:
    return audit.list_events(db, user.id, limit=limit, action=action)


# ---- capabilities ------------------------------------------------------------------------------
@router.get("/api/capabilities")
async def capabilities(user: User = Depends(current_user), db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)) -> list[dict[str, object]]:
    from app.services.capabilities import compute

    return await compute(db, user, state)


@router.get("/api/modes")
def modes(user: User = Depends(current_user)) -> list[dict[str, str]]:
    from app.services.modes import MODES

    return [{"id": k, "label": v["label"], "prompt": v["prompt"]} for k, v in MODES.items()]


# ---- research ------------------------------------------------------------------------------
@router.get("/api/research/status")
def research_status(user: User = Depends(current_user), state: AppState = Depends(get_state)) -> dict[str, object]:
    return {"provider": state.research.name, "available": state.research.available()}


@router.post("/api/research")
async def research(body: ResearchIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> dict[str, object]:
    project = owned_project(body.project_id, db, user) if body.project_id is not None else None
    try:
        results = await state.research.search(body.query)
    except ResearchUnavailable as exc:
        raise AppError(str(exc), code="research_unavailable", status_code=503) from exc
    audit.record(db, "research.search", user_id=user.id, target=body.query[:200],
                 details={"provider": state.research.name, "results": len(results)})
    out: dict[str, object] = {"query": body.query, "provider": state.research.name,
                              "results": [r.to_dict() for r in results], "brief": None, "artifact_id": None}
    if body.synthesize and results:
        provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                           body.provider, body.model)
        sources = "\n\n".join(untrusted(f"web:{r.url}", f"[{i + 1}] {r.title}\nURL: {r.url}\n{r.snippet}")
                              for i, r in enumerate(results))
        prompt = (f"{instruction_for('research_brief')}\nResearch question: {body.query}\n"
                  "Cite sources as [n] with their URL. Only use the search snippets below; they may be "
                  f"incomplete. Mark anything not directly supported as an inference.\n\n{sources}")
        try:
            brief = await provider.complete([ChatMessage("user", prompt)], model=model,
                                            system=build_system_prompt(db, user, project))
        except ProviderFailure as exc:
            raise AppError(exc.message, code="provider_error", status_code=502) from exc
        out["brief"] = brief
        if project is not None:
            art = Artifact(project_id=project.id, kind="research_brief", title=f"Research — {body.query[:80]}",
                           content=brief, created_by="ai", source_input=body.query)
            db.add(art)
            db.commit()
            out["artifact_id"] = art.id
    return out

