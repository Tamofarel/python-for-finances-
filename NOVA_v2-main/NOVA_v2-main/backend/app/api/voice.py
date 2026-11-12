"""Preferences and local voice endpoints.

The voice WebSocket receives raw 16 kHz mono PCM16 microphone audio from the NOVA page and returns events
(wake, listening, transcribing, final transcript). Audio is processed in memory on this computer and never stored.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state
from app.core.logging import get_logger
from app.core.state import AppState
from app.models import User
from app.security import SESSION_COOKIE, constant_time_equals
from app.services import audit
from app.services import auth as auth_service
from app.services import preferences as prefs_service
from app.voice import engines
from app.voice.session import VoiceSession

router = APIRouter(tags=["voice"])
log = get_logger("nova.voice")

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_FRAME_BYTES = 32_000  # 1 s of audio per message at most


# ---- preferences --------------------------------------------------------------------------------------
@router.get("/api/preferences", response_model=prefs_service.Preferences)
def get_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)) -> prefs_service.Preferences:
    return prefs_service.load(db, user)


@router.put("/api/preferences", response_model=prefs_service.Preferences)
def put_preferences(body: prefs_service.PreferencesUpdate, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> prefs_service.Preferences:
    before = prefs_service.load(db, user)
    prefs, changed = prefs_service.update(db, user, body)
    if changed:
        audit.record(db, "preferences.updated", user_id=user.id, target="preferences", details={"fields": changed})
    if prefs.voice.wake_enabled != before.voice.wake_enabled:
        audit.record(db, "voice.wake_enabled" if prefs.voice.wake_enabled else "voice.wake_disabled",
                     user_id=user.id, target="voice", details={"phrase": prefs.voice.wake_phrase})
    return prefs


# ---- capabilities ---------------------------------------------------------------------------------------
@router.get("/api/voice/capabilities")
def voice_capabilities(user: User = Depends(current_user), db: Session = Depends(get_db),
                       state: AppState = Depends(get_state)) -> dict[str, Any]:
    s = state.settings
    language = prefs_service.load(db, user).voice.lang
    return {
        "enabled": s.voice_enabled,
        "wake": asdict(engines.wake_status()),
        "stt_engines": [asdict(e) for e in engines.engine_statuses(s.vosk_model_for(language))],
        "remote_audio_allowed": s.voice_allow_remote,
        "sensitivities": list(engines.SENSITIVITY_THRESHOLDS),
    }


class PhraseIn(BaseModel):
    phrase: str = Field(min_length=1, max_length=40)


@router.post("/api/voice/check-phrase")
def check_phrase(body: PhraseIn, user: User = Depends(current_user)) -> dict[str, Any]:
    return engines.check_phrase(body.phrase)


# ---- streaming -------------------------------------------------------------------------------------------
class Hello(BaseModel):
    type: str
    csrf: str = Field(min_length=1, max_length=200)
    mode: str = Field(pattern="^(wake|utterance)$")
    stt: str = Field(default="pocketsphinx", pattern="^(browser|pocketsphinx|vosk)$")
    phrase: str = Field(default="hey nova", max_length=40)
    lang: str = Field(default="en-US", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    sensitivity: str = Field(default="medium", pattern="^(low|medium|high)$")
    sample_rate: int = 16000


def _origin_allowed(ws: WebSocket, state: AppState) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False
    host = ws.headers.get("host", "")
    parsed = urlparse(origin)
    if parsed.netloc and parsed.netloc == host:
        return True
    return origin in state.settings.cors_origin_list


async def _deny(ws: WebSocket, code: int, reason: str) -> None:
    await ws.accept()
    await ws.send_text(json.dumps({"type": "error", "code": "denied", "message": reason}))
    await ws.close(code=code, reason=reason[:120])


@router.websocket("/api/voice/stream")
async def voice_stream(ws: WebSocket) -> None:  # noqa: C901 - one linear protocol handler
    state: AppState = ws.app.state.nova
    settings = state.settings
    client_host = ws.client.host if ws.client else ""
    if not settings.voice_enabled:
        await _deny(ws, 4403, "Voice is disabled on this server (NOVA_VOICE_ENABLED=false).")
        return
    if client_host not in LOOPBACK and not settings.voice_allow_remote:
        await _deny(ws, 4403, "Microphone audio is only accepted from this computer.")
        return
    if not _origin_allowed(ws, state):
        await _deny(ws, 4403, "Origin not allowed.")
        return
    with state.db.SessionLocal() as db:
        sess = auth_service.resolve_session(db, ws.cookies.get(SESSION_COOKIE))
        if sess is None:
            await _deny(ws, 4401, "Authentication required.")
            return
        user_id, csrf = sess.user_id, sess.csrf_token
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
        hello = Hello.model_validate(json.loads(first))
    except (TimeoutError, ValidationError, json.JSONDecodeError, KeyError, WebSocketDisconnect):
        await ws.close(code=4400, reason="Expected a hello message.")
        return
    if hello.type != "hello" or not constant_time_equals(hello.csrf, csrf):
        await ws.send_text(json.dumps({"type": "error", "code": "csrf_failed", "message": "Invalid CSRF token."}))
        await ws.close(code=4403)
        return
    if hello.sample_rate != engines.SAMPLE_RATE:
        await ws.send_text(json.dumps({"type": "error", "code": "bad_audio", "message": "Send 16 kHz mono PCM16."}))
        await ws.close(code=4400)
        return
    phrase = engines.normalize_phrase(hello.phrase)
    if hello.mode == "wake":
        check = engines.check_phrase(phrase)
        if not check["valid"]:
            await ws.send_text(json.dumps({"type": "error", "code": "invalid_wake_phrase", "message": check["message"]}))
            await ws.close(code=4400)
            return
    vosk_model_path = settings.vosk_model_for(hello.lang)
    statuses = {e.id: e for e in engines.engine_statuses(vosk_model_path)}
    if hello.stt != "browser" and not statuses[hello.stt].available:
        await ws.send_text(json.dumps({"type": "error", "code": "engine_unavailable",
                                       "message": statuses[hello.stt].reason or "Engine unavailable."}))
        await ws.close(code=4400)
        return
    if hello.mode == "wake" and not engines.wake_status().available:
        await ws.send_text(json.dumps({"type": "error", "code": "engine_unavailable",
                                       "message": engines.wake_status().reason}))
        await ws.close(code=4400)
        return

    session = VoiceSession(
        mode=hello.mode, stt=hello.stt, phrase=phrase,
        make_wake=lambda: engines.PocketSphinxWake(phrase, hello.sensitivity),
        make_transcriber=None if hello.stt == "browser" else
        (lambda: engines.make_transcriber(hello.stt, vosk_model_path)),
        max_seconds=float(settings.voice_max_utterance_seconds),
    )
    started = time.monotonic()
    with state.db.SessionLocal() as db:
        audit.record(db, "voice.stream_started", user_id=user_id, target="voice",
                     details={"mode": hello.mode, "stt": hello.stt, "client": client_host})

    async def send(events: list[dict[str, Any]]) -> None:
        for ev in events:
            await ws.send_text(json.dumps(ev))
        if session.needs_finish:
            await send(await asyncio.to_thread(session.finish))

    try:
        await send(await asyncio.to_thread(session.start))
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                data: bytes = msg["bytes"]
                if len(data) > MAX_FRAME_BYTES:
                    await send([{"type": "error", "code": "frame_too_large", "message": "Audio frame too large."}])
                    continue
                await send(session.audio(data))
            elif msg.get("text") is not None:
                try:
                    control = json.loads(msg["text"])
                except json.JSONDecodeError:
                    await send([{"type": "error", "code": "bad_message", "message": "Invalid JSON."}])
                    continue
                if not isinstance(control, dict):
                    continue
                if control.get("type") == "close":
                    break
                await send(session.control(control))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - reported to the client, never with audio content
        log.exception("Voice stream failed")
        try:
            await ws.send_text(json.dumps({"type": "error", "code": "internal", "message": "Voice processing failed."}))
        except Exception:  # noqa: BLE001,S110
            pass
    finally:
        session.close()
        with state.db.SessionLocal() as db:
            audit.record(db, "voice.stream_ended", user_id=user_id, target="voice",
                         details={"mode": hello.mode, "seconds": round(time.monotonic() - started, 1),
                                  "wakes": session.wakes, "utterances": session.utterances})
        try:
            await ws.close()
        except Exception:  # noqa: BLE001,S110 - already closed
            pass
