from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import Auth, client_ip, get_auth, get_db, get_state
from app.core.errors import TooManyRequests
from app.core.state import AppState
from app.schemas import AuthStatus, ChangePasswordIn, LoginIn, SetupIn, UserOut
from app.security import SESSION_COOKIE
from app.services import audit
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, state: AppState, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=state.settings.cookie_secure, max_age=state.settings.session_ttl_hours * 3600,
                        path="/")


@router.get("/status", response_model=AuthStatus)
def status(request: Request, db: Session = Depends(get_db)) -> AuthStatus:
    initialized = auth_service.user_count(db) > 0
    sess = auth_service.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        return AuthStatus(initialized=initialized, authenticated=False)
    return AuthStatus(initialized=True, authenticated=True, user=UserOut.model_validate(sess.user),
                      csrf_token=sess.csrf_token)


@router.post("/setup", response_model=AuthStatus, status_code=201)
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db),
          state: AppState = Depends(get_state)) -> AuthStatus:
    ip = client_ip(request) or "unknown"
    if not state.setup_limiter.hit(ip):
        raise TooManyRequests("Too many setup attempts. Wait a few minutes.")
    try:
        user = auth_service.create_owner(db, username=body.username, password=body.password,
                                         display_name=body.display_name, setup_code=state.setup_code,
                                         provided_code=body.setup_code)
    except Exception:
        audit.record(db, "auth.setup", user_id=None, outcome="denied", ip=ip)
        raise
    token, csrf = auth_service.start_session(db, user, state.settings.session_ttl_hours)
    _set_session_cookie(response, state, token)
    audit.record(db, "auth.setup", user_id=user.id, target=user.username, ip=ip)
    return AuthStatus(initialized=True, authenticated=True, user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/login", response_model=AuthStatus)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db),
          state: AppState = Depends(get_state)) -> AuthStatus:
    ip = client_ip(request) or "unknown"
    if not state.login_limiter.hit(f"{ip}:{body.username.lower()}"):
        audit.record(db, "auth.login", user_id=None, target=body.username, outcome="denied",
                     details={"reason": "rate_limited"}, ip=ip)
        raise TooManyRequests("Too many login attempts. Wait a few minutes.")
    try:
        user = auth_service.authenticate(db, body.username, body.password)
    except Exception:
        audit.record(db, "auth.login", user_id=None, target=body.username, outcome="failure", ip=ip)
        raise
    token, csrf = auth_service.start_session(db, user, state.settings.session_ttl_hours)
    _set_session_cookie(response, state, token)
    audit.record(db, "auth.login", user_id=user.id, target=user.username, ip=ip)
    return AuthStatus(initialized=True, authenticated=True, user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, auth: Auth = Depends(get_auth),
           db: Session = Depends(get_db)) -> Response:
    auth_service.end_session(db, auth.session)
    audit.record(db, "auth.logout", user_id=auth.user.id, ip=client_ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.post("/password", response_model=AuthStatus)
def change_password(body: ChangePasswordIn, request: Request, response: Response, auth: Auth = Depends(get_auth),
                    db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> AuthStatus:
    user = auth.user
    auth_service.change_password(db, user, body.current_password, body.new_password)
    token, csrf = auth_service.start_session(db, user, state.settings.session_ttl_hours)
    _set_session_cookie(response, state, token)
    audit.record(db, "auth.password_changed", user_id=user.id, ip=client_ip(request))
    return AuthStatus(initialized=True, authenticated=True, user=UserOut.model_validate(user), csrf_token=csrf)
