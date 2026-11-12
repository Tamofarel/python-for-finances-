"""Local accounts and server-side sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, Unauthorized
from app.models import User, UserSession
from app.security import (
    constant_time_equals,
    dummy_verify,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)


def user_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def create_owner(db: Session, *, username: str, password: str, display_name: str,
                 setup_code: str, provided_code: str) -> User:
    if user_count(db) > 0:
        raise Conflict("NOVA is already initialized.", code="already_initialized")
    if not constant_time_equals(setup_code, provided_code.strip()):
        raise Forbidden("Invalid setup code. Check the backend console output.", code="invalid_setup_code")
    user = User(username=username.lower(), display_name=display_name or username,
                password_hash=hash_password(password), is_owner=True)
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username.lower()))
    if user is None:
        dummy_verify()
        raise Unauthorized("Invalid username or password.", code="invalid_credentials")
    if not verify_password(user.password_hash, password):
        raise Unauthorized("Invalid username or password.", code="invalid_credentials")
    return user


def start_session(db: Session, user: User, ttl_hours: int) -> tuple[str, str]:
    """Create a session; returns (raw session token, csrf token). Only the token digest is stored."""
    token = new_token()
    csrf = new_token()
    now = datetime.now(UTC)
    db.add(UserSession(user_id=user.id, token_hash=token_digest(token), csrf_token=csrf,
                       created_at=now, expires_at=now + timedelta(hours=ttl_hours), last_seen_at=now))
    db.execute(delete(UserSession).where(UserSession.expires_at < now))
    db.commit()
    return token, csrf


def resolve_session(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token)))
    if sess is None:
        return None
    expires = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        db.delete(sess)
        db.commit()
        return None
    return sess


def end_session(db: Session, sess: UserSession) -> None:
    db.delete(sess)
    db.commit()


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(user.password_hash, current):
        raise Unauthorized("Current password is incorrect.", code="invalid_credentials")
    user.password_hash = hash_password(new)
    # Invalidate every session for this user; the caller starts a fresh one.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
