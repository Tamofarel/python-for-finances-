from __future__ import annotations

from app.providers.base import ProviderFailure
from tests.conftest import make_client, parse_sse


def _new(client, title="New conversation", **kw):
    r = client.post("/api/conversations", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_conversation_crud_and_search(authed):
    a = _new(authed, "Alpha planning")
    b = _new(authed, "Beta")
    assert {c["id"] for c in authed.get("/api/conversations").json()} == {a["id"], b["id"]}
    r = authed.patch(f"/api/conversations/{b['id']}", json={"title": "Renamed"})
    assert r.json()["title"] == "Renamed"
    assert [c["id"] for c in authed.get("/api/conversations", params={"q": "alpha"}).json()] == [a["id"]]
    assert authed.delete(f"/api/conversations/{a['id']}").status_code == 204
    assert authed.get(f"/api/conversations/{a['id']}").status_code == 404


def test_send_message_streams_and_persists(authed, provider):
    provider.replies = ["Hello Farel, NOVA here."]
    conv = _new(authed)
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Hi NOVA\nsecond line"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "delta" in kinds
    assert "".join(d["text"] for e, d in events if e == "delta") == "Hello Farel, NOVA here."
    detail = authed.get(f"/api/conversations/{conv['id']}").json()
    assert detail["title"] == "Hi NOVA"  # auto-title from first line
    assert [(m["role"], m["status"]) for m in detail["messages"]] == [("user", "complete"), ("assistant", "complete")]
    assert detail["messages"][1]["content"] == "Hello Farel, NOVA here."
    assert detail["messages"][1]["provider"] == "scripted"
    # Timestamps are serialized as timezone-aware UTC so browsers show the right local time.
    assert detail["messages"][0]["created_at"].endswith(("Z", "+00:00"))
    assert detail["created_at"].endswith(("Z", "+00:00"))


def test_history_is_sent_to_provider(authed, provider):
    conv = _new(authed)
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "first"})
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "second"})
    last = provider.calls[-1]["messages"]
    assert [m.content for m in last] == ["first", "echo: first", "second"]
    assert "You are NOVA" in provider.calls[-1]["system"]


def test_search_matches_message_content(authed):
    conv = _new(authed, "Untitled thing")
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "talk about quaternions"})
    hits = authed.get("/api/conversations", params={"q": "quaternion"}).json()
    assert [c["id"] for c in hits] == [conv["id"]]


def test_conversations_survive_restart(settings, provider, authed):
    conv = _new(authed, "Persistent")
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "remember me"})
    cookie = authed.cookies.get("nova_session")
    csrf = authed.headers["X-CSRF-Token"]
    fresh = make_client(settings, provider)  # new app instance over the same database
    fresh.cookies.set("nova_session", cookie)
    fresh.headers["X-CSRF-Token"] = csrf
    detail = fresh.get(f"/api/conversations/{conv['id']}").json()
    assert [m["content"] for m in detail["messages"]] == ["remember me", "echo: remember me"]


def test_provider_failure_is_reported_not_faked(authed, provider):
    provider.fail_with = ProviderFailure("Cannot connect to Ollama.", kind="unreachable", retryable=True)
    conv = _new(authed)
    events = parse_sse(authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi"}).text)
    err = [d for e, d in events if e == "error"]
    assert err and err[0]["message"] == "Cannot connect to Ollama." and err[0]["retryable"] is True
    msgs = authed.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert msgs[-1]["status"] == "error" and msgs[-1]["content"] == ""
    assert msgs[-1]["error"] == "Cannot connect to Ollama."


def test_retry_after_failure(authed, provider):
    provider.fail_with = ProviderFailure("boom")
    conv = _new(authed)
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "question"})
    provider.fail_with = None
    provider.replies = ["answer"]
    events = parse_sse(authed.post(f"/api/conversations/{conv['id']}/retry", json={}).text)
    assert events[-1][0] == "done"
    msgs = authed.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [(m["role"], m["content"], m["status"]) for m in msgs] == [
        ("user", "question", "complete"), ("assistant", "answer", "complete")]


def test_unconfigured_provider_is_rejected(authed):
    conv = _new(authed)
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi", "provider": "unconfigured"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "provider_not_configured"
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi", "provider": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_provider"


def test_message_validation(authed):
    conv = _new(authed)
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": ""})
    assert r.status_code == 422


def test_provider_selection_persisted(authed):
    r = authed.get("/api/providers").json()
    names = {p["name"]: p for p in r["providers"]}
    assert names["scripted"]["reachable"] is True
    assert names["unconfigured"]["configured"] is False
    assert authed.put("/api/providers/selection", json={"provider": "scripted", "model": "scripted-1"}).status_code == 200
    assert authed.get("/api/providers").json()["selected"] == {"provider": "scripted", "model": "scripted-1"}
    assert authed.put("/api/providers/selection", json={"provider": "ghost", "model": "x"}).status_code == 404
