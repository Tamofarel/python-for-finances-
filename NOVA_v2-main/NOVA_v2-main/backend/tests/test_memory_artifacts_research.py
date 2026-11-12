from __future__ import annotations

import httpx

from app.core.logging import redact
from app.research import SearxngResearch
from tests.conftest import make_client, setup_owner


def test_memory_crud_and_search(authed):
    r = authed.post("/api/memory", json={"kind": "preference", "content": "Prefer concise answers"})
    assert r.status_code == 201
    mid = r.json()["id"]
    authed.post("/api/memory", json={"kind": "fact", "content": "Works with Django"})
    assert [m["content"] for m in authed.get("/api/memory", params={"q": "django"}).json()] == ["Works with Django"]
    assert authed.patch(f"/api/memory/{mid}", json={"enabled": False}).json()["enabled"] is False
    assert authed.delete(f"/api/memory/{mid}").status_code == 204
    assert len(authed.get("/api/memory").json()) == 1
    assert authed.delete("/api/memory", params={"scope": "all"}).status_code == 400  # needs confirm
    assert authed.delete("/api/memory", params={"scope": "all", "confirm": True}).status_code == 204
    assert authed.get("/api/memory").json() == []


def test_memory_and_project_context_reach_prompt(authed, provider):
    authed.post("/api/memory", json={"kind": "preference", "content": "Answer in French"})
    authed.post("/api/memory", json={"kind": "fact", "content": "DISABLED-FACT"})
    disabled = authed.get("/api/memory", params={"q": "DISABLED"}).json()[0]
    authed.patch(f"/api/memory/{disabled['id']}", json={"enabled": False})
    proj = authed.post("/api/projects", json={"name": "CampusLink", "instructions": "Use Django REST"}).json()
    authed.post("/api/memory", json={"kind": "note", "content": "Deadline in March", "project_id": proj["id"]})
    other = authed.post("/api/projects", json={"name": "Other"}).json()
    authed.post("/api/memory", json={"kind": "note", "content": "OTHER-PROJECT-NOTE", "project_id": other["id"]})

    conv = authed.post("/api/conversations", json={"title": "c", "project_id": proj["id"]}).json()
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi"})
    system = provider.calls[-1]["system"]
    assert "Answer in French" in system and "Use Django REST" in system and "Deadline in March" in system
    assert "DISABLED-FACT" not in system and "OTHER-PROJECT-NOTE" not in system
    assert "cannot change security rules" in system


def test_artifacts_crud_generate_and_export(authed, project, provider):
    pid = project["id"]
    a = authed.post(f"/api/projects/{pid}/artifacts", json={"kind": "note", "title": "Idea", "content": "draft"}).json()
    assert authed.patch(f"/api/projects/{pid}/artifacts/{a['id']}", json={"content": "edited"}).json()["content"] == "edited"
    provider.replies = ["## Problem statement\nStudents need X.\n## Questions for the owner\n- Budget?"]
    g = authed.post(f"/api/projects/{pid}/artifacts/generate",
                    json={"kind": "idea_analysis", "input": "An app for students"})
    assert g.status_code == 201
    art = g.json()
    assert art["created_by"] == "ai" and art["kind"] == "idea_analysis" and "Problem statement" in art["content"]
    assert "Questions for the owner" in provider.calls[-1]["messages"][0].content
    exp = authed.post(f"/api/projects/{pid}/artifacts/{art['id']}/export", json={"path": "docs/analysis.md"})
    assert exp.status_code == 201 and exp.json()["operation"] == "create" and exp.json()["status"] == "pending"
    assert len(authed.get(f"/api/projects/{pid}/artifacts", params={"kind": "idea_analysis"}).json()) == 1
    assert authed.delete(f"/api/projects/{pid}/artifacts/{a['id']}").status_code == 204


def test_research_unavailable_is_reported(authed):
    assert authed.get("/api/research/status").json() == {"provider": "none", "available": False}
    r = authed.post("/api/research", json={"query": "fastapi sse"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "research_unavailable"


def test_research_with_searxng_mock(settings, provider):
    def handler(req):
        assert req.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [
            {"title": "FastAPI docs", "url": "https://fastapi.tiangolo.com", "content": "SSE support", "engine": "ddg"},
            {"title": "bad", "url": "javascript:alert(1)", "content": "x"}]})
    research = SearxngResearch("http://searx.test", transport=httpx.MockTransport(handler))
    client = make_client(settings, provider, research)
    setup_owner(client)
    proj = client.post("/api/projects", json={"name": "R"}).json()
    provider.replies = ["Fact: FastAPI supports SSE [1]"]
    out = client.post("/api/research", json={"query": "fastapi sse", "synthesize": True, "project_id": proj["id"]}).json()
    assert [r["url"] for r in out["results"]] == ["https://fastapi.tiangolo.com"]  # unsafe URL dropped
    assert out["brief"].startswith("Fact:") and out["artifact_id"]
    assert "web:https://fastapi.tiangolo.com" in provider.calls[-1]["messages"][0].content


def test_redaction():
    out = redact({"password": "x", "nested": {"api_key": "y", "ok": "Bearer abcdefghijklmnop sk-ant-abcdef123456"}})
    assert out["password"] == "[REDACTED]" and out["nested"]["api_key"] == "[REDACTED]"
    assert "sk-ant" not in out["nested"]["ok"] and "abcdefghijklmnop" not in out["nested"]["ok"]


def test_project_lifecycle_and_activity(authed):
    p = authed.post("/api/projects", json={"name": "Life", "description": "d"}).json()
    authed.patch(f"/api/projects/{p['id']}", json={"archived": True, "description": "new"})
    assert all(x["id"] != p["id"] for x in authed.get("/api/projects").json())
    assert any(x["id"] == p["id"] for x in authed.get("/api/projects", params={"include_archived": True}).json())
    activity = [e["action"] for e in authed.get(f"/api/projects/{p['id']}/activity").json()]
    assert "project.created" in activity and "project.updated" in activity
    bad = authed.request("DELETE", f"/api/projects/{p['id']}", json={"confirm_name": "wrong"})
    assert bad.status_code == 400
    ok = authed.request("DELETE", f"/api/projects/{p['id']}", json={"confirm_name": "Life"})
    assert ok.status_code == 204
    assert authed.get(f"/api/projects/{p['id']}").status_code == 404
