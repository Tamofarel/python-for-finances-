"""Per-user preferences (theme, motion, voice, proactivity), stored server-side in app_settings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import User
from app.services.assistant import get_setting, set_setting
from app.voice.engines import check_phrase, normalize_phrase

KEY = "preferences"


class VoicePrefs(BaseModel):
    wake_enabled: bool = False
    wake_phrase: str = Field(default="hey nova", max_length=40)
    sensitivity: Literal["low", "medium", "high"] = "medium"
    stt_engine: Literal["browser", "pocketsphinx", "vosk"] = "browser"
    tts_voice: str | None = Field(default=None, max_length=200)
    speak_replies: bool = True  # answer voice requests aloud (typed requests are never read out automatically)
    acknowledge: bool = True
    chime: bool = True
    follow_up: bool = False
    lang: str = Field(default="en-US", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")

    @field_validator("wake_phrase")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_phrase(v)


class Preferences(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    motion: Literal["system", "reduce", "full"] = "system"
    suggestions: bool = True
    interaction_mode: Literal["collaborative", "quiet", "step_by_step"] = "collaborative"
    voice: VoicePrefs = Field(default_factory=VoicePrefs)
    wake_consent_at: str | None = None


class VoicePrefsUpdate(BaseModel):
    wake_enabled: bool | None = None
    wake_phrase: str | None = Field(default=None, max_length=40)
    sensitivity: Literal["low", "medium", "high"] | None = None
    stt_engine: Literal["browser", "pocketsphinx", "vosk"] | None = None
    tts_voice: str | None = Field(default=None, max_length=200)
    speak_replies: bool | None = None
    acknowledge: bool | None = None
    chime: bool | None = None
    follow_up: bool | None = None
    lang: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")


class PreferencesUpdate(BaseModel):
    theme: Literal["system", "light", "dark"] | None = None
    motion: Literal["system", "reduce", "full"] | None = None
    suggestions: bool | None = None
    interaction_mode: Literal["collaborative", "quiet", "step_by_step"] | None = None
    voice: VoicePrefsUpdate | None = None
    wake_consent: bool | None = None  # must be true in the request that first enables the wake phrase


def load(db: Session, user: User) -> Preferences:
    raw = get_setting(db, user, KEY)
    try:
        return Preferences.model_validate(raw) if isinstance(raw, dict) else Preferences()
    except ValueError:
        return Preferences()  # stored data from an older version: fall back to defaults


def update(db: Session, user: User, body: PreferencesUpdate) -> tuple[Preferences, list[str]]:
    prefs = load(db, user)
    data: dict[str, Any] = prefs.model_dump()
    changed: list[str] = []
    for key in ("theme", "motion", "suggestions", "interaction_mode"):
        value = getattr(body, key)
        if value is not None and value != data[key]:
            data[key] = value
            changed.append(key)
    if body.voice is not None:
        for key, value in body.voice.model_dump(exclude_unset=True).items():
            if key == "tts_voice" or value is not None:
                if data["voice"].get(key) != value:
                    data["voice"][key] = value
                    changed.append(f"voice.{key}")
    voice = data["voice"]
    if "voice.wake_phrase" in changed:
        result = check_phrase(voice["wake_phrase"])
        if not result["valid"]:
            raise AppError(str(result["message"]), code="invalid_wake_phrase", details=result)
    if voice["wake_enabled"] and not prefs.voice.wake_enabled and not data.get("wake_consent_at"):
        if not body.wake_consent:
            raise AppError("Enabling the wake phrase keeps the microphone open while NOVA is open. "
                           "Confirm that you agree (wake_consent: true).", code="consent_required")
        data["wake_consent_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        changed.append("wake_consent_at")
    new = Preferences.model_validate(data)
    set_setting(db, user, KEY, new.model_dump())
    return new, changed
