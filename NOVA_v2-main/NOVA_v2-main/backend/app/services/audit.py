"""Audit trail. Details are redacted before storage; secrets must never reach this table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, redact
from app.models import AuditEvent

log = get_logger("nova.audit")


def record(
    db: Session,
    action: str,
    *,
    user_id: int | None,
    target: str = "",
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    commit: bool = True,
) -> AuditEvent:
    ev = AuditEvent(
        user_id=user_id,
        action=action,
        target=target[:255],
        outcome=outcome,
        details=redact(details or {}),
        ip=ip,
    )
    db.add(ev)
    if commit:
        db.commit()
    log.info("audit %s %s", action, outcome, extra={"extra_data": {"target": target, "user_id": user_id}})
    return ev


def list_events(db: Session, user_id: int, *, limit: int = 100, action: str | None = None) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.user_id == user_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    return list(db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(min(limit, 500))))
