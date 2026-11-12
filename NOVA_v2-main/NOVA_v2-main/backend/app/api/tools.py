"""Authenticated access to the explicit local-tool registry."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state
from app.core.state import AppState
from app.models import User
from app.services import audit
from app.services.agent import is_paused
from app.tools.registry import ToolContext
from app.tools.spotify import SpotifyProblem

router = APIRouter(tags=["tools"])


class ToolExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=20)


@router.get("/api/tools")
def list_tools(user: User = Depends(current_user), state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    return state.tools.schemas()


@router.post("/api/tools/execute")
def execute_tool(
    body: ToolExecution, user: User = Depends(current_user), db: Session = Depends(get_db), state: AppState = Depends(get_state)
) -> dict[str, Any]:
    """Run one routine tool directly (buttons in the UI). Safety-critical tools are refused here: they need approval."""
    ctx = ToolContext(user.id, time.monotonic() + 60, is_paused(db, user))
    state.action_runs.add(user.id, ctx)
    try:
        result = state.tools.execute(body.name, body.arguments, ctx)
    finally:
        state.action_runs.remove(user.id, ctx)
    audit.record(
        db,
        f"tool.{body.name}",
        user_id=user.id,
        target="desktop_tools",
        outcome=result.status,
        details={"duration_ms": result.duration_ms, "status": result.status, "via": "direct"},
    )
    return {"status": result.status, "message": result.message, "data": result.data, "duration_ms": result.duration_ms}


@router.get("/api/spotify/status")
def spotify_status(user: User = Depends(current_user), state: AppState = Depends(get_state)) -> dict[str, object]:
    assert state.spotify is not None
    return state.spotify.status()


@router.get("/api/spotify/connect")
def spotify_connect(user: User = Depends(current_user), state: AppState = Depends(get_state)) -> RedirectResponse:
    assert state.spotify is not None
    try:
        target = state.spotify.begin(user.id)
    except SpotifyProblem:
        return RedirectResponse("/?spotify=configuration_error", status_code=303)
    if urlparse(target).netloc != "accounts.spotify.com":
        return RedirectResponse("/?spotify=configuration_error", status_code=303)
    return RedirectResponse(target, status_code=303)


@router.get("/api/spotify/callback", include_in_schema=False)
def spotify_callback(request: Request, state: AppState = Depends(get_state)) -> RedirectResponse:
    """OAuth state is one-use and tied to an owner-initiated PKCE flow; Spotify redirects cross-site."""
    assert state.spotify is not None
    if request.query_params.get("error"):
        return RedirectResponse("/?spotify=denied", status_code=303)
    code, oauth_state = request.query_params.get("code"), request.query_params.get("state")
    if not code or not oauth_state or len(code) > 2000 or len(oauth_state) > 200:
        return RedirectResponse("/?spotify=failed", status_code=303)
    try:
        owner_id = state.spotify.finish(code, oauth_state)
    except (SpotifyProblem, ValueError):
        return RedirectResponse("/?spotify=failed", status_code=303)
    with state.db.SessionLocal() as db:
        audit.record(db, "spotify.connected", user_id=owner_id, target="spotify", details={"method": "authorization_code_pkce"})
    return RedirectResponse("/?spotify=connected", status_code=303)


@router.post("/api/spotify/disconnect")
def spotify_disconnect(
    user: User = Depends(current_user), db: Session = Depends(get_db), state: AppState = Depends(get_state)
) -> dict[str, bool]:
    assert state.spotify is not None
    state.spotify.disconnect()
    audit.record(db, "spotify.disconnected", user_id=user.id, target="spotify")
    return {"connected": False}
