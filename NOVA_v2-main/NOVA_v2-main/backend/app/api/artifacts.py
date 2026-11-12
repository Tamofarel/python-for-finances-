from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.api.projects import project_target
from app.core.errors import AppError, NotFound
from app.core.state import AppState
from app.models import Artifact, ChangeProposal, User
from app.providers.base import ChatMessage, ProviderFailure
from app.schemas import (
    ArtifactCreate,
    ArtifactExportIn,
    ArtifactGenerateIn,
    ArtifactOut,
    ArtifactUpdate,
    ChangeOut,
)
from app.services import audit, changes
from app.services.artifacts import instruction_for, label_for
from app.services.assistant import build_system_prompt, resolve_provider

router = APIRouter(prefix="/api/projects/{project_id}/artifacts", tags=["artifacts"])


def _get(db: Session, project_id: int, artifact_id: int) -> Artifact:
    art = db.get(Artifact, artifact_id)
    if art is None or art.project_id != project_id:
        raise NotFound("Artifact not found.")
    return art


@router.get("", response_model=list[ArtifactOut])
def list_artifacts(project_id: int, kind: str | None = Query(default=None, max_length=40),
                   q: str | None = Query(default=None, max_length=200), user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> list[Artifact]:
    owned_project(project_id, db, user)
    stmt = select(Artifact).where(Artifact.project_id == project_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if q:
        stmt = stmt.where(or_(Artifact.title.ilike(f"%{q}%"), Artifact.content.ilike(f"%{q}%")))
    return list(db.scalars(stmt.order_by(Artifact.updated_at.desc())))


@router.post("", response_model=ArtifactOut, status_code=201)
def create_artifact(project_id: int, body: ArtifactCreate, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> Artifact:
    proj = owned_project(project_id, db, user)
    art = Artifact(project_id=proj.id, kind=body.kind, title=body.title, content=body.content, created_by="user")
    db.add(art)
    db.commit()
    audit.record(db, "artifact.created", user_id=user.id, target=project_target(proj.id),
                 details={"artifact_id": art.id, "kind": art.kind})
    return art


@router.post("/generate", response_model=ArtifactOut, status_code=201)
async def generate_artifact(project_id: int, body: ArtifactGenerateIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> Artifact:
    proj = owned_project(project_id, db, user)
    provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                       body.provider, body.model)
    prompt = f"{instruction_for(body.kind)}\n\nInput from the user:\n{body.input}"
    try:
        content = await provider.complete([ChatMessage("user", prompt)], model=model,
                                          system=build_system_prompt(db, user, proj))
    except ProviderFailure as exc:
        raise AppError(exc.message, code="provider_error", status_code=502) from exc
    if not content.strip():
        raise AppError("The provider returned an empty response.", code="provider_error", status_code=502)
    title = body.title or f"{label_for(body.kind)} — {body.input.strip().splitlines()[0][:60]}"
    art = Artifact(project_id=proj.id, kind=body.kind, title=title, content=content, created_by="ai",
                   source_input=body.input)
    db.add(art)
    db.commit()
    audit.record(db, "artifact.generated", user_id=user.id, target=project_target(proj.id),
                 details={"artifact_id": art.id, "kind": art.kind, "provider": provider.name, "model": model})
    return art


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(project_id: int, artifact_id: int, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> Artifact:
    owned_project(project_id, db, user)
    return _get(db, project_id, artifact_id)


@router.patch("/{artifact_id}", response_model=ArtifactOut)
def update_artifact(project_id: int, artifact_id: int, body: ArtifactUpdate, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> Artifact:
    owned_project(project_id, db, user)
    art = _get(db, project_id, artifact_id)
    if body.title is not None:
        art.title = body.title
    if body.content is not None:
        art.content = body.content
    db.commit()
    audit.record(db, "artifact.updated", user_id=user.id, target=project_target(project_id),
                 details={"artifact_id": art.id})
    return art


@router.delete("/{artifact_id}", status_code=204)
def delete_artifact(project_id: int, artifact_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> Response:
    owned_project(project_id, db, user)
    art = _get(db, project_id, artifact_id)
    db.delete(art)
    db.commit()
    audit.record(db, "artifact.deleted", user_id=user.id, target=project_target(project_id),
                 details={"artifact_id": artifact_id})
    return Response(status_code=204)


@router.post("/{artifact_id}/export", response_model=ChangeOut, status_code=201)
def export_artifact(project_id: int, artifact_id: int, body: ArtifactExportIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ChangeProposal:
    """Export an artifact into the workspace as a reviewable change proposal."""
    proj = owned_project(project_id, db, user)
    art = _get(db, project_id, artifact_id)
    ws = changes.workspace_for(proj, state.settings.max_file_bytes)
    cp = changes.propose(db, proj, ws, path=body.path, content=art.content,
                         summary=f"Export artifact '{art.title}'", source="user")
    audit.record(db, "change.proposed", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id, "artifact_id": art.id})
    return cp
