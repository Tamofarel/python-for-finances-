"""Permission model.

Category A (read-only): runs automatically within the owner's workspace scope.
Category B (reversible changes): produce a proposal; applying it requires approval bound to its content hash.
Category C (consequential): an ActionRequest that requires explicit confirmation bound to a hash of the
exact parameters, immediately before execution. One approval executes one action, once.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, NotFound
from app.models import ActionRequest, Project, User

ACTION_TTL = timedelta(minutes=10)

CATEGORIES: dict[str, dict[str, Any]] = {
    "read_file": {"category": "A", "reversible": True},
    "list_files": {"category": "A", "reversible": True},
    "propose_change": {"category": "B", "reversible": True},
    "apply_change": {"category": "B", "reversible": True},
    "create_file": {"category": "B", "reversible": True},
    "run_command": {"category": "C", "reversible": False},
    "delete_path": {"category": "C", "reversible": False},
    "move_path": {"category": "B", "reversible": True},
    # A safety-critical assistant action (for example unlocking a door), re-run exactly as approved, once.
    "tool_call": {"category": "C", "reversible": False},
}


def params_hash(kind: str, project_id: int | None, params: dict[str, Any]) -> str:
    blob = json.dumps({"kind": kind, "project_id": project_id, "params": params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def create_request(db: Session, user: User, project: Project | None, kind: str, params: dict[str, Any],
                   reason: str) -> ActionRequest:
    meta = CATEGORIES[kind]
    project_id = project.id if project is not None else None
    ar = ActionRequest(owner_id=user.id, project_id=project_id, kind=kind, category=meta["category"],
                       params=params, params_hash=params_hash(kind, project_id, params), reason=reason,
                       reversible=meta["reversible"])
    db.add(ar)
    db.commit()
    return ar


def get_request(db: Session, user: User, action_id: int) -> ActionRequest:
    ar = db.get(ActionRequest, action_id)
    if ar is None or ar.owner_id != user.id:
        raise NotFound("Action request not found.")
    return ar


def consume_approval(db: Session, ar: ActionRequest, provided_hash: str) -> None:
    """Validate an approval and mark the request approved (single use)."""
    if ar.status != "pending":
        raise Conflict(f"Action is already {ar.status}.", code="not_pending")
    created = ar.created_at if ar.created_at.tzinfo else ar.created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > ACTION_TTL:
        ar.status = "expired"
        db.commit()
        raise Conflict("This action request expired. Create a new one.", code="expired")
    expected = params_hash(ar.kind, ar.project_id, ar.params)
    if provided_hash != ar.params_hash or expected != ar.params_hash:
        raise AppError("Approval does not match the requested action parameters.",
                       code="approval_mismatch", status_code=403)
    ar.status = "approved"
    ar.decided_at = datetime.now(UTC)
    db.commit()


def reject(db: Session, ar: ActionRequest) -> None:
    if ar.status != "pending":
        raise Conflict(f"Action is already {ar.status}.", code="not_pending")
    ar.status = "rejected"
    ar.decided_at = datetime.now(UTC)
    db.commit()
