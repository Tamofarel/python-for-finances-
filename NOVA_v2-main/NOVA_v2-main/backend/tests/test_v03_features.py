"""0.3 features: preferences, capability reporting, image attachments, conversation modes, move/rename."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import httpx2

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ImageInput
from app.providers.ollama import OllamaProvider
from tests.conftest import make_client, parse_sse, setup_owner

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


# ---- preferences ------------------------------------------------------------------------------------------
def test_preferences_defaults_and_updates(authed):
    p = authed.get("/api/preferences").json()
    assert p["theme"] == "system" and p["motion"] == "system" and p["voice"]["wake_enabled"] is False
    assert p["interaction_mode"] == "collaborative"
    r = authed.put("/api/preferences", json={"theme": "light", "motion": "reduce", "interaction_mode": "step_by_step",
                                             "voice": {"stt_engine": "pocketsphinx", "speak_replies": True}})
    assert r.status_code == 200
    p = authed.get("/api/preferences").json()
    assert p["theme"] == "light" and p["motion"] == "reduce"
    assert p["interaction_mode"] == "step_by_step"
    assert p["voice"]["stt_engine"] == "pocketsphinx" and p["voice"]["speak_replies"] is True
    assert authed.put("/api/preferences", json={"theme": "neon"}).status_code == 422


def test_wake_requires_consent_and_valid_phrase(authed):
    r = authed.put("/api/preferences", json={"voice": {"wake_enabled": True}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"
    bad = authed.put("/api/preferences", json={"voice": {"wake_phrase": "hey zxqvbn"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_wake_phrase"
    ok = authed.put("/api/preferences", json={"voice": {"wake_enabled": True, "wake_phrase": "Hey NOVA"},
                                              "wake_consent": True}).json()
    assert ok["voice"]["wake_enabled"] is True and ok["voice"]["wake_phrase"] == "hey nova" and ok["wake_consent_at"]
    # Turning it off and on again does not ask again; consent stays recorded.
    authed.put("/api/preferences", json={"voice": {"wake_enabled": False}})
    assert authed.put("/api/preferences", json={"voice": {"wake_enabled": True}}).status_code == 200
    actions = [e["action"] for e in authed.get("/api/audit").json()]
    assert "voice.wake_enabled" in actions and "voice.wake_disabled" in actions


# ---- capabilities --------------------------------------------------------------------------------------------
def test_capabilities_offline_are_reported_honestly(settings, provider):
    c = make_client(settings.model_copy(update={"default_provider": "unconfigured"}), provider)
    setup_owner(c)
    caps = {x["id"]: x for x in c.get("/api/capabilities").json()}
    assert caps["conversation"]["status"] == "unavailable" and caps["conversation"]["reason"]
    assert caps["product_discovery"]["status"] == "limited"  # workflows still work from your answers
    assert caps["research"]["status"] == "unavailable"
    assert caps["files"]["status"] == "available" and caps["automation"]["status"] == "available"
    assert caps["vision"]["status"] == "unavailable"
    assert caps["voice"]["status"] == "available"


def test_capabilities_with_model_and_vision(authed, provider):
    caps = {x["id"]: x for x in authed.get("/api/capabilities").json()}
    assert caps["conversation"]["status"] == "available"
    assert caps["vision"]["status"] == "limited"  # the provider does not say whether the model reads images
    provider.vision = False
    caps = {x["id"]: x for x in authed.get("/api/capabilities").json()}
    assert caps["vision"]["status"] == "unavailable" and "does not read images" in caps["vision"]["reason"]
    provider.vision = True
    caps = {x["id"]: x for x in authed.get("/api/capabilities").json()}
    assert caps["vision"]["status"] == "available"


# ---- attachments and vision -------------------------------------------------------------------------------------
def _conv(c):
    return c.post("/api/conversations", json={"title": "img"}).json()


def _upload(c, conv_id, data=PNG, name="shot.png"):
    return c.post(f"/api/conversations/{conv_id}/attachments", files={"file": (name, data, "image/png")})


def test_image_attachment_reaches_the_provider(authed, provider):
    provider.vision = True
    conv = _conv(authed)
    att = _upload(authed, conv["id"]).json()
    assert att["mime"] == "image/png" and att["size"] == len(PNG)
    events = parse_sse(authed.post(f"/api/conversations/{conv['id']}/messages",
                                   json={"content": "What is in this screenshot?", "attachment_ids": [att["id"]]}).text)
    assert events[-1][0] == "done"
    sent = provider.calls[-1]["messages"][-1]
    assert sent.images and sent.images[0].mime == "image/png" and sent.images[0].data == PNG
    detail = authed.get(f"/api/conversations/{conv['id']}").json()
    assert detail["messages"][0]["attachments"][0]["id"] == att["id"]
    img = authed.get(f"/api/attachments/{att['id']}")
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"
    # The same image cannot be attached to another message.
    again = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x", "attachment_ids": [att["id"]]})
    assert again.status_code == 400 and again.json()["error"]["code"] == "attachment_mismatch"


def test_attachment_validation(authed):
    conv = _conv(authed)
    fake = _upload(authed, conv["id"], data=b"<svg onload=alert(1)>", name="x.png")
    assert fake.status_code == 415  # the content is checked, not the file name or declared type
    big = _upload(authed, conv["id"], data=PNG + b"\x00" * 5_100_000)
    assert big.status_code == 413
    assert _upload(authed, conv["id"], data=b"").status_code == 400


def test_vision_unsupported_model_is_refused_before_sending(authed, provider):
    provider.vision = False
    conv = _conv(authed)
    att = _upload(authed, conv["id"]).json()
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "look", "attachment_ids": [att["id"]]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "vision_unsupported"
    assert authed.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_attachments_are_private_and_deleted_with_conversation(authed, settings, provider):
    conv = _conv(authed)
    att = _upload(authed, conv["id"]).json()
    stored = list((settings.uploads_dir).rglob("*.png"))
    assert len(stored) == 1
    from app.models import User
    from app.security import hash_password
    with authed.app.state.nova.db.SessionLocal() as db:
        db.add(User(username="other", password_hash=hash_password("other-password-1")))
        db.commit()
    other = make_client(settings, provider)
    r = other.post("/api/auth/login", json={"username": "other", "password": "other-password-1"})
    other.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert other.get(f"/api/attachments/{att['id']}").status_code == 404
    assert authed.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert not stored[0].exists()


def test_provider_payloads_include_images():
    seen = {}

    def ollama(req):
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        seen["ollama"] = json.loads(req.content)
        return httpx.Response(200, content=b'{"message":{"content":"ok"},"done":true}\n')

    import asyncio
    p = OllamaProvider("http://o.test", "llava", transport=httpx.MockTransport(ollama))
    msg = ChatMessage("user", "describe", images=(ImageInput("image/png", PNG),))
    assert asyncio.run(p.complete([msg], model="llava", system="")) == "ok"
    assert seen["ollama"]["messages"][0]["images"] == [base64.b64encode(PNG).decode()]
    assert asyncio.run(p.supports_images("llava")) is True

    def anthropic_handler(req):
        seen["anthropic"] = json.loads(req.content)
        return httpx2.Response(401, json={"type": "error", "error": {"type": "x", "message": "no"}})

    a = AnthropicProvider("sk-ant-test-not-real", "claude-x",
                          client_kwargs={"http_client": httpx2.AsyncClient(transport=httpx2.MockTransport(anthropic_handler))})
    try:
        asyncio.run(a.complete([msg], model="claude-x", system=""))
    except Exception:  # noqa: BLE001 - the mocked API answers 401; we only inspect the request
        pass
    block = seen["anthropic"]["messages"][0]["content"]
    assert block[0]["type"] == "image" and block[0]["source"]["media_type"] == "image/png"
    assert block[1] == {"type": "text", "text": "describe"}


# ---- conversation modes ------------------------------------------------------------------------------------------
def test_modes_change_the_system_prompt(authed, provider):
    modes = {m["id"] for m in authed.get("/api/modes").json()}
    assert {"general", "engineering", "learning", "business", "design", "research", "product"} <= modes
    conv = authed.post("/api/conversations", json={"title": "teach me", "mode": "learning"}).json()
    assert conv["mode"] == "learning"
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "explain recursion"})
    assert "Mode: learning" in provider.calls[-1]["system"]
    authed.patch(f"/api/conversations/{conv['id']}", json={"mode": "engineering"})
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "and now?"})
    assert "Mode: software engineering" in provider.calls[-1]["system"]
    assert authed.patch(f"/api/conversations/{conv['id']}", json={"mode": "hacker"}).status_code == 422


# ---- move / rename --------------------------------------------------------------------------------------------------
def test_move_requires_approval_and_never_overwrites(authed, project):
    pid, ws = project["id"], Path(project["workspace_path"])
    authed.post(f"/api/projects/{pid}/files", json={"path": "notes.md", "content": "a\n"})
    authed.post(f"/api/projects/{pid}/files", json={"path": "other.md", "content": "b\n"})
    clash = authed.post(f"/api/projects/{pid}/actions/move", json={"src": "notes.md", "dst": "other.md"})
    assert clash.status_code == 409
    bad = authed.post(f"/api/projects/{pid}/actions/move", json={"src": "notes.md", "dst": "../escape.md"})
    assert bad.status_code == 403
    action = authed.post(f"/api/projects/{pid}/actions/move", json={"src": "notes.md", "dst": "docs/notes.md"}).json()
    assert action["kind"] == "move_path" and action["status"] == "pending"
    assert (ws / "notes.md").exists()  # nothing moved before approval
    r = authed.post(f"/api/actions/{action['id']}/approve", json={"params_hash": action["params_hash"], "confirm": True})
    assert r.json()["result"] == {"moved": "notes.md", "to": "docs/notes.md"}
    assert (ws / "docs" / "notes.md").read_text() == "a\n" and not (ws / "notes.md").exists()
