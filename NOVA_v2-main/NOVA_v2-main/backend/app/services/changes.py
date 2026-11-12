"""Reviewable change proposals: nothing overwrites an existing file without an approved proposal.

Approval is bound to the exact proposed content (its SHA-256) and to the file's state when the
proposal was made (base SHA-256). If the file changed in between, the proposal becomes stale.
The original content is kept as a backup so applied changes can be reverted.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, NotFound
from app.models import ChangeProposal, Project
from app.workspaces import Workspace, sha256_text, unified_diff


def workspace_for(project: Project, max_bytes: int) -> Workspace:
    ws = Workspace(project.workspace_path, max_bytes)
    ws.ensure_root()
    return ws


def propose(db: Session, project: Project, ws: Workspace, *, path: str, content: str, summary: str,
            source: str, base_sha256: str | None = None) -> ChangeProposal:
    clean, target = ws.resolve(path)
    if not clean:
        raise AppError("A file path is required.", code="path_required")
    if target.is_dir():
        raise AppError("Path is a folder.", code="is_directory")
    if target.exists():
        _, original = ws.read_text(clean)
        current_sha = sha256_text(original)
        if base_sha256 and base_sha256 != current_sha:
            raise Conflict("The file changed since you opened it. Reload before proposing changes.",
                           code="stale_base")
        operation = "modify"
        if original == content:
            raise AppError("The proposed content is identical to the current file.", code="no_changes")
    else:
        original, current_sha, operation = None, None, "create"
    diff = unified_diff(clean, original, content)
    cp = ChangeProposal(project_id=project.id, path=clean, operation=operation, original_content=original,
                        base_sha256=current_sha, proposed_content=content, proposed_sha256=sha256_text(content),
                        diff=diff, summary=summary.strip(), source=source)
    db.add(cp)
    db.commit()
    return cp


def get_proposal(db: Session, project: Project, change_id: int) -> ChangeProposal:
    cp = db.get(ChangeProposal, change_id)
    if cp is None or cp.project_id != project.id:
        raise NotFound("Change proposal not found.")
    return cp


def apply(db: Session, ws: Workspace, cp: ChangeProposal, approved_sha256: str) -> ChangeProposal:
    if cp.status != "pending":
        raise Conflict(f"Proposal is already {cp.status}.", code="not_pending")
    if approved_sha256 != cp.proposed_sha256:
        raise AppError("Approval does not match the proposed content.", code="approval_mismatch",
                       status_code=403)
    _, target = ws.resolve(cp.path)
    current = ws.read_text(cp.path)[1] if target.exists() else None
    current_sha = sha256_text(current) if current is not None else None
    if current_sha != cp.base_sha256:
        cp.status = "stale"
        cp.decided_at = datetime.now(UTC)
        db.commit()
        raise Conflict("The file changed after this proposal was created. It was marked stale.",
                       code="stale_base")
    ws.write_text(cp.path, cp.proposed_content)
    cp.status = "applied"
    cp.decided_at = datetime.now(UTC)
    db.commit()
    return cp


def reject(db: Session, cp: ChangeProposal) -> ChangeProposal:
    if cp.status != "pending":
        raise Conflict(f"Proposal is already {cp.status}.", code="not_pending")
    cp.status = "rejected"
    cp.decided_at = datetime.now(UTC)
    db.commit()
    return cp


def revert(db: Session, ws: Workspace, cp: ChangeProposal) -> ChangeProposal:
    """Restore the backup of an applied change, only if the file still holds the applied content."""
    if cp.status != "applied":
        raise Conflict("Only applied changes can be reverted.", code="not_applied")
    _, current = ws.read_text(cp.path)
    if sha256_text(current) != cp.proposed_sha256:
        raise Conflict("The file was modified after the change was applied; revert manually.",
                       code="stale_base")
    if cp.original_content is None:
        ws.delete(cp.path)
    else:
        ws.write_text(cp.path, cp.original_content)
    cp.status = "reverted"
    db.commit()
    return cp
