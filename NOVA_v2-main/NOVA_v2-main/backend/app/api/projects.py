from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.core.errors import AppError
from app.core.state import AppState
from app.models import AuditEvent, Project, User
from app.schemas import AuditOut, DeleteConfirm, ProjectCreate, ProjectOut, ProjectUpdate
from app.services import audit
from app.workspaces import validate_workspace_path

router = APIRouter(prefix="/api/projects", tags=["projects"])


def project_target(project_id: int, rest: str = "") -> str:
    return f"project:{project_id}" + (f":{rest}" if rest else "")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return s or "project"


@router.get("", response_model=list[ProjectOut])
def list_projects(include_archived: bool = False, q: str | None = Query(default=None, max_length=200),
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Project]:
    stmt = select(Project).where(Project.owner_id == user.id)
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    if q:
        stmt = stmt.where(Project.name.ilike(f"%{q}%") | Project.description.ilike(f"%{q}%"))
    return list(db.scalars(stmt.order_by(Project.updated_at.desc())))


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> Project:
    if body.workspace_path:
        path = validate_workspace_path(body.workspace_path, state.settings.workspace_roots)
    else:
        path = (state.settings.default_workspaces_dir / f"{_slug(body.name)}-{secrets.token_hex(3)}").resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AppError(f"Cannot create workspace folder: {exc.strerror}", code="workspace_error") from exc
    proj = Project(owner_id=user.id, name=body.name, description=body.description,
                   instructions=body.instructions, workspace_path=str(path))
    db.add(proj)
    db.commit()
    audit.record(db, "project.created", user_id=user.id, target=project_target(proj.id), details={"name": proj.name})
    return proj


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    return owned_project(project_id, db, user)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectUpdate, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Project:
    proj = owned_project(project_id, db, user)
    changed = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changed.items():
        setattr(proj, key, value)
    db.commit()
    audit.record(db, "project.updated", user_id=user.id, target=project_target(proj.id),
                 details={"fields": sorted(changed)})
    return proj


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, body: DeleteConfirm, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    """Deletes NOVA's records for the project. Workspace files on disk are intentionally kept."""
    proj = owned_project(project_id, db, user)
    if body.confirm_name != proj.name:
        audit.record(db, "project.deleted", user_id=user.id, target=project_target(proj.id), outcome="denied",
                     details={"reason": "confirmation_mismatch"})
        raise AppError("Type the exact project name to confirm deletion.", code="confirmation_mismatch")
    db.delete(proj)
    db.commit()
    audit.record(db, "project.deleted", user_id=user.id, target=project_target(project_id),
                 details={"name": body.confirm_name, "workspace_kept": True})
    return Response(status_code=204)


@router.get("/{project_id}/activity", response_model=list[AuditOut])
def project_activity(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[AuditEvent]:
    owned_project(project_id, db, user)
    prefix = project_target(project_id)
    stmt = (select(AuditEvent).where(AuditEvent.user_id == user.id,
                                     (AuditEvent.target == prefix) | AuditEvent.target.like(prefix + ":%"))
            .order_by(AuditEvent.id.desc()).limit(200))
    return list(db.scalars(stmt))
