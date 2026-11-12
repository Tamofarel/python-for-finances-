"""Shared FastAPI dependencies: state, DB session, authentication, CSRF."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.state import AppState
from app.models import Project, User, UserSession
from app.security import CSRF_HEADER, SESSION_COOKIE, constant_time_equals
from app.services import auth as auth_service

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_state(request: Request) -> AppState:
    return request.app.state.nova


def get_db(state: AppState = Depends(get_state)) -> Iterator[Session]:
    yield from state.db.session()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@dataclass
class Auth:
    user: User
    session: UserSession


def get_auth(request: Request, db: Session = Depends(get_db)) -> Auth:
    sess = auth_service.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise Unauthorized("Authentication required.")
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_equals(header, sess.csrf_token):
            raise Forbidden("Missing or invalid CSRF token.", code="csrf_failed")
    return Auth(user=sess.user, session=sess)


def current_user(auth: Auth = Depends(get_auth)) -> User:
    return auth.user


def owned_project(project_id: int, db: Session, user: User) -> Project:
    """Authorization check: a project is only visible to its owner (404 avoids leaking existence)."""
    proj = db.get(Project, project_id)
    if proj is None or proj.owner_id != user.id:
        raise NotFound("Project not found.")
    return proj
