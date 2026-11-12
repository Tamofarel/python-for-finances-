"""Idea-to-product workflow tests. AI behaviour uses the TEST-ONLY scripted provider."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.research import SearxngResearch
from tests.conftest import make_client, setup_owner

PLANT_IDEA = ("I want a smartphone app that uses a photograph of a plant to estimate whether it is healthy and "
              "identify possible visible disease symptoms.")


def _client(settings, provider, research=None, default="scripted"):
    settings = settings.model_copy(update={"default_provider": default})
    c = make_client(settings, provider, research)
    setup_owner(c)
    return c


@pytest.fixture
def offline(settings, provider):
    """No AI available: the default provider is not configured."""
    return _client(settings, provider, default="unconfigured")


def _create(c, idea=PLANT_IDEA, template="ml_vision_app", **kw):
    r = c.post("/api/workflows", json={"idea": idea, "template": template, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _open_q(wf):
    return next(q for q in wf["questions"] if q["status"] == "open")


def _answer_all(c, wf, answers=None, limit=20):
    for _ in range(limit):
        q = next((q for q in wf["questions"] if q["status"] == "open"), None)
        if q is None:
            break
        text = (answers or {}).get(q["topic"], f"Answer about {q['topic']}")
        wf = c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": text}).json()
    return wf


def test_templates_are_listed(authed):
    ids = {t["id"] for t in authed.get("/api/workflow-templates").json()}
    assert {"general", "software_product", "ml_vision_app", "marketing_campaign", "research_study", "business_plan"} <= ids


def test_offline_workflow_asks_template_questions_one_at_a_time(offline):
    wf = _create(offline)
    assert wf["status"] == "active" and wf["current_stage"] == "clarify"
    assert [s["status"] for s in wf["stages"][:2]] == ["done", "running"]
    open_qs = [q for q in wf["questions"] if q["status"] == "open"]
    assert len(open_qs) == 1  # never all questions at once
    # 0.3: NOVA first restates the idea and asks the owner to confirm it (no AI: deterministic restatement).
    intent = open_qs[0]
    assert intent["topic"] == "intent" and intent["source"] == "template"
    assert "You want a smartphone app that uses a photograph of a plant" in intent["question"]
    assert intent["options"] == ["Yes, that's right"]
    assert any("No AI model is available" in e["message"] for e in wf["events"])
    wf = offline.post(f"/api/workflows/{wf['id']}/questions/{intent['id']}/answer",
                      json={"answer": "Yes, that's right"}).json()
    # The plant scenario asks who the users are, then which plants.
    assert [q["status"] for q in wf["questions"]] == ["answered", "open"]
    assert _open_q(wf)["topic"] == "users"
    wf = offline.post(f"/api/workflows/{wf['id']}/questions/{_open_q(wf)['id']}/answer",
                      json={"answer": "Home gardeners"}).json()
    assert _open_q(wf)["topic"] == "subject_scope"
    again = offline.post(f"/api/workflows/{wf['id']}/questions/{intent['id']}/answer", json={"answer": "x"})
    assert again.status_code == 409


def test_ai_question_is_validated_and_used(settings, provider):
    c = _client(settings, provider)
    provider.replies = [json.dumps({"summary": "You want an app that estimates plant health from a photo."}),
                        json.dumps({"topic": "classes", "question": "Which tomato diseases matter most to you?",
                                    "why": "Classes define the dataset.", "options": ["Early blight", "Late blight"]})]
    wf = _create(c)
    intent = _open_q(wf)
    assert intent["source"] == "ai" and "estimates plant health from a photo" in intent["question"]
    assert "<untrusted_data" in provider.calls[-1]["messages"][0].content  # the idea is fenced as data
    wf = c.post(f"/api/workflows/{wf['id']}/questions/{intent['id']}/answer", json={"answer": "Yes, that's right"}).json()
    q = _open_q(wf)
    assert q["source"] == "ai" and q["topic"] == "classes" and q["options"] == ["Early blight", "Late blight"]
    assert "Prefer one of these open topics" in provider.calls[-1]["messages"][0].content


def test_invalid_ai_question_falls_back_to_template(settings, provider):
    c = _client(settings, provider)
    provider.replies = ["not json either", "Sure! Here is my question: what plants?"]  # neither reply is JSON
    wf = _create(c)
    intent = _open_q(wf)
    assert intent["topic"] == "intent" and intent["source"] == "template"  # restated, not invented
    assert any(e["kind"] == "error" and "AI summary failed" in e["message"] for e in wf["events"])
    wf = c.post(f"/api/workflows/{wf['id']}/questions/{intent['id']}/answer", json={"answer": "Yes"}).json()
    assert _open_q(wf)["source"] == "template"
    assert any(e["kind"] == "error" and "not valid JSON" in e["message"] for e in wf["events"])


def test_clarify_requires_an_answer_and_skips_open_questions(offline):
    wf = _create(offline)
    r = offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_answers"
    q = _open_q(wf)
    offline.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": "Tomatoes"})
    wf = offline.post(f"/api/workflows/{wf['id']}/clarify/complete").json()
    assert wf["stages"][1]["status"] == "done" and wf["next_stage"] == "research"
    assert all(q["status"] != "open" for q in wf["questions"])
    assert {m["topic"] for m in wf["missing_decisions"]} >= {"classes", "dataset"}


def _run(c, wf_id, stage, mode="auto"):
    r = c.post(f"/api/workflows/{wf_id}/stages/{stage}/run", params={"wait": True}, json={"mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_offline_workflow_creates_editable_artifacts_and_tasks(offline, settings, provider):
    wf = _create(offline)
    wf = _answer_all(offline, wf, {"subject_scope": "Tomato leaves, home gardeners", "privacy": "Photos stay on the phone"})
    assert wf["clarify_ready"] is True
    wf = offline.post(f"/api/workflows/{wf['id']}/clarify/complete").json()
    wf = _run(offline, wf["id"], "research")
    research = wf["stages"][2]
    assert research["status"] == "done"
    assert all(f["status"] == "pending" and not f["results"] for f in wf["research"])  # nothing invented
    assert research["artifacts"][0]["kind"] == "research_plan"
    plan = offline.get(f"/api/projects/{wf['project_id']}/artifacts/{research['artifacts'][0]['id']}").json()
    assert "Research is pending" in plan["content"] and "dataset cards" in plan["content"]

    for stage in ("analysis", "architecture", "ux", "technology", "artifacts"):
        wf = offline.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True}, json={"mode": "auto"}).json()
        assert next(s for s in wf["stages"] if s["id"] == stage)["status"] == "done", stage
    kinds = {a["kind"] for s in wf["stages"] for a in s["artifacts"]}
    assert {"feasibility", "architecture", "data_model", "api_contract", "ui_spec", "technology_choices",
            "requirements", "user_stories", "implementation_plan", "risk_register"} <= kinds
    req_id = next(a["id"] for s in wf["stages"] for a in s["artifacts"] if a["kind"] == "requirements")
    req = offline.get(f"/api/projects/{wf['project_id']}/artifacts/{req_id}").json()
    assert "No AI model was used" in req["content"]
    assert "Tomato leaves, home gardeners" in req["content"]
    assert "visible features only" in req["content"]  # template guidance: no definitive health claim
    # Editable like any artifact.
    assert offline.patch(f"/api/projects/{wf['project_id']}/artifacts/{req_id}", json={"content": "edited"}).status_code == 200

    wf = offline.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True}, json={"mode": "auto"}).json()
    assert wf["status"] == "awaiting_decision" and wf["proposal"]["sha256"]
    assert "research questions have real sources" in wf["proposal"]["content"]

    # Persistence across a backend restart.
    fresh = make_client(settings.model_copy(update={"default_provider": "unconfigured"}), provider)
    fresh.cookies.set("nova_session", offline.cookies.get("nova_session"))
    fresh.headers["X-CSRF-Token"] = offline.headers["X-CSRF-Token"]
    again = fresh.get(f"/api/workflows/{wf['id']}").json()
    assert again["status"] == "awaiting_decision" and len(again["questions"]) == len(wf["questions"])

    bad = fresh.post(f"/api/workflows/{wf['id']}/decision", json={"decision": "approve", "proposal_sha256": "0" * 64})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "approval_mismatch"
    ok = fresh.post(f"/api/workflows/{wf['id']}/decision",
                    json={"decision": "approve", "proposal_sha256": wf["proposal"]["sha256"]}).json()
    assert ok["status"] == "approved" and len(ok["work_items"]) == 7
    items = fresh.get("/api/work", params={"workflow_id": wf["id"]}).json()
    assert items[0]["source"] == "template" and items[1]["depends_on"] == [items[0]["id"]]
    assert all(i["executor"] == "manual" for i in items)


def test_proposal_edit_invalidates_approval(offline):
    wf = _create(offline, idea="A newsletter to promote a local bakery's new pastries.", template="marketing_campaign")
    wf = _answer_all(offline, wf)
    offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    for _ in range(7):
        wf = offline.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True}, json={"mode": "template"}).json()
        if wf["status"] == "awaiting_decision":
            break
    stages = {s["id"]: s for s in wf["stages"]}
    assert stages["architecture"]["status"] == "skipped"  # not relevant for a marketing campaign
    old_sha = wf["proposal"]["sha256"]
    offline.patch(f"/api/projects/{wf['project_id']}/artifacts/{wf['proposal']['artifact_id']}", json={"content": "changed"})
    r = offline.post(f"/api/workflows/{wf['id']}/decision", json={"decision": "approve", "proposal_sha256": old_sha})
    assert r.status_code == 409


def test_revise_and_reject(offline):
    wf = _create(offline, idea="A study on how students use AI tools for homework.", template="research_study")
    wf = _answer_all(offline, wf)
    offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    wf = _run(offline, wf["id"], "proposal")
    assert wf["status"] == "awaiting_decision"
    r = offline.post(f"/api/workflows/{wf['id']}/decision", json={"decision": "revise", "feedback": ""})
    assert r.status_code == 400
    wf = offline.post(f"/api/workflows/{wf['id']}/decision",
                      json={"decision": "revise", "feedback": "Focus on first-year students", "reopen_stage": "analysis"}).json()
    stages = {s["id"]: s["status"] for s in wf["stages"]}
    assert wf["status"] == "active" and stages["analysis"] == "pending" and stages["proposal"] == "pending"
    assert wf["revision_notes"][-1]["feedback"] == "Focus on first-year students"
    wf = _run(offline, wf["id"], "analysis")
    feas = next(a for s in wf["stages"] for a in s["artifacts"] if a["kind"] == "feasibility")
    content = offline.get(f"/api/projects/{wf['project_id']}/artifacts/{feas['id']}").json()["content"]
    assert "Focus on first-year students" in content
    wf = _run(offline, wf["id"], "proposal")
    wf = offline.post(f"/api/workflows/{wf['id']}/decision", json={"decision": "reject", "feedback": "Not now"}).json()
    assert wf["status"] == "rejected"
    assert offline.post(f"/api/workflows/{wf['id']}/stages/analysis/run", json={"mode": "template"}).status_code == 409


def test_edited_artifact_is_not_overwritten(offline):
    wf = _create(offline, template="general", idea="An app to share rides between students.")
    wf = _answer_all(offline, wf)
    offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    wf = _run(offline, wf["id"], "analysis")
    art = next(a for s in wf["stages"] for a in s["artifacts"] if a["kind"] == "feasibility")
    offline.patch(f"/api/projects/{wf['project_id']}/artifacts/{art['id']}", json={"content": "MY OWN EDITS"})
    wf = _run(offline, wf["id"], "analysis")
    assert offline.get(f"/api/projects/{wf['project_id']}/artifacts/{art['id']}").json()["content"] == "MY OWN EDITS"
    new = next(a for s in wf["stages"] for a in s["artifacts"] if a["kind"] == "feasibility")
    assert new["id"] != art["id"] and new["title"].endswith("(revision)")


def test_ai_stages_use_real_research_and_rules(settings, provider):
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "PlantDoc dataset paper", "url": "https://arxiv.org/abs/1911.10317", "content": "field images", "engine": "x"}]})
    research = SearxngResearch("http://searx.test", transport=httpx.MockTransport(handler))
    c = _client(settings, provider, research)
    provider.replies = ['{"topic": "subject_scope", "question": "Which plants?", "why": "", "options": []}']
    wf = _create(c)
    q = _open_q(wf)
    provider.replies = ['{"enough": true}']
    wf = c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": "Tomatoes"}).json()
    # The model may only stop when the template topics are covered, so NOVA keeps asking.
    assert _open_q(wf)["source"] == "template"
    c.post(f"/api/workflows/{wf['id']}/clarify/complete")
    provider.replies = ["## Research brief\n**Verified**: field images exist (https://arxiv.org/abs/1911.10317)"]
    wf = _run(c, wf["id"], "research", mode="ai")
    assert all(f["status"] == "sourced" for f in wf["research"])
    assert wf["research"][0]["results"][0]["url"] == "https://arxiv.org/abs/1911.10317"
    brief = wf["stages"][2]["artifacts"][0]
    assert brief["kind"] == "research_brief" and brief["created_by"] == "ai"
    prompt = provider.calls[-1]["messages"][0].content
    assert "<untrusted_data" in prompt and "Never invent sources" in prompt
    provider.replies = ["# Feasibility\n**Assumption**: ..."]
    wf = _run(c, wf["id"], "analysis", mode="ai")
    assert wf["stages"][3]["artifacts"][0]["created_by"] == "ai" and wf["stages"][3]["mode"] == "ai"


def test_ai_mode_reports_unavailable_provider(offline):
    wf = _create(offline)
    offline.post(f"/api/workflows/{wf['id']}/questions/{_open_q(wf)['id']}/answer", json={"answer": "x"})
    offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    wf = _run(offline, wf["id"], "analysis", mode="ai")
    st = wf["stages"][3]
    assert st["status"] == "failed" and "not configured" in st["error"] and not st["artifacts"]


def test_ai_generated_tasks_cannot_become_commands(settings, provider):
    c = _client(settings, provider)
    provider.replies = ["not json"]
    wf = _create(c, template="general", idea="A small tool to track house plants watering.")
    q = _open_q(wf)
    provider.replies = ["not json"]
    c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": "Me"})
    c.post(f"/api/workflows/{wf['id']}/clarify/complete")
    provider.replies = ["# Plan\n1. Do things"] * 4
    _run(c, wf["id"], "artifacts", mode="ai")
    wf = _run(c, wf["id"], "proposal")
    provider.replies = [json.dumps([{"title": "Delete everything", "executor": "command", "argv": ["rm", "-rf", "/"],
                                     "domain": "engineering"}, {"title": "Write tests", "depends_on": [0]}])]
    wf = c.post(f"/api/workflows/{wf['id']}/decision",
                json={"decision": "approve", "proposal_sha256": wf["proposal"]["sha256"]}).json()
    items = c.get("/api/work", params={"workflow_id": wf["id"]}).json()
    assert [i["title"] for i in items] == ["Delete everything", "Write tests"]
    assert all(i["executor"] == "manual" and not i["requires_approval"] and "argv" not in i["inputs"] for i in items)
    assert items[0]["source"] == "ai" and items[1]["depends_on"] == [items[0]["id"]]


def test_running_stage_can_be_cancelled(settings, provider):
    c = _client(settings, provider)
    provider.replies = ["not json"]
    wf = _create(c, template="general", idea="A tool for organising family recipes.")
    c.post(f"/api/workflows/{wf['id']}/questions/{_open_q(wf)['id']}/answer", json={"answer": "Families"})
    c.post(f"/api/workflows/{wf['id']}/clarify/complete")
    provider.delay = 5
    started = c.post(f"/api/workflows/{wf['id']}/stages/analysis/run", json={"mode": "ai"}).json()
    assert started["running"] is True
    assert c.post(f"/api/workflows/{wf['id']}/stages/ux/run", json={"mode": "ai"}).status_code == 409
    time.sleep(0.3)
    assert c.post(f"/api/workflows/{wf['id']}/cancel").status_code == 200
    for _ in range(50):
        wf = c.get(f"/api/workflows/{wf['id']}").json()
        if not wf["running"]:
            break
        time.sleep(0.1)
    assert wf["stages"][3]["status"] == "cancelled"


def test_interrupted_stage_recovered_after_restart(offline, settings, provider):
    wf = _create(offline)
    from app.models import Workflow
    with offline.app.state.nova.db.SessionLocal() as db:
        row = db.get(Workflow, wf["id"])
        row.stage_state = {**row.stage_state, "research": {"status": "running"}}
        db.commit()
    fresh = make_client(settings, provider)
    fresh.cookies.set("nova_session", offline.cookies.get("nova_session"))
    st = {s["id"]: s for s in fresh.get(f"/api/workflows/{wf['id']}").json()["stages"]}
    assert st["research"]["status"] == "interrupted" and "stopped" in st["research"]["error"]


def test_workflows_are_private(offline, settings, provider):
    wf = _create(offline)
    from app.models import User
    from app.security import hash_password
    with offline.app.state.nova.db.SessionLocal() as db:
        db.add(User(username="other", password_hash=hash_password("other-password-1")))
        db.commit()
    other = make_client(settings, provider)
    r = other.post("/api/auth/login", json={"username": "other", "password": "other-password-1"})
    other.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert other.get(f"/api/workflows/{wf['id']}").status_code == 404
    assert other.post(f"/api/workflows/{wf['id']}/clarify/complete").status_code == 404
    assert other.get("/api/workflows").json() == []


def test_create_validation(offline):
    assert offline.post("/api/workflows", json={"idea": "short"}).status_code == 422
    assert offline.post("/api/workflows", json={"idea": "A long enough idea text", "template": "nope"}).status_code == 400


# ---- 0.3: flexible workflow -------------------------------------------------------------------------------
PLANT_VOICE = ("NOVA, I want to build an app that helps people determine whether their plants are healthy using their "
               "smartphone camera.")


def test_restate_and_template_suggestion():
    from app.workflows.engine import restate
    from app.workflows.templates import suggest_template
    assert restate(PLANT_VOICE).startswith("You want to build an app that helps people determine whether their plants")
    assert restate("I have an idea for my bakery") == "Your idea: your bakery."
    assert restate("Hey Nova, we want to launch our shop") == "You want to launch your shop."
    assert restate("Je veux créer une application") == "Vous voulez créer une application."
    assert suggest_template(PLANT_VOICE)[0] == "ml_vision_app"
    assert suggest_template("A campaign to promote my bakery on Instagram")[0] == "marketing_campaign"
    assert suggest_template("Une étude sur les étudiants et l'IA")[0] == "research_study"
    assert suggest_template("A website for booking tailors")[0] == "software_product"
    assert suggest_template("Something nice for my family")[0] == "general"


def test_auto_template_and_dismiss(offline):
    s = offline.get("/api/workflow-templates/suggest", params={"idea": PLANT_VOICE}).json()
    assert s["template"] == "ml_vision_app" and "camera" in s["reason"]
    wf = _create(offline, idea=PLANT_VOICE, template="auto")
    assert wf["template"] == "ml_vision_app"
    assert "You want to build an app" in _open_q(wf)["question"]
    out = offline.post(f"/api/workflows/{wf['id']}/dismiss").json()
    assert out["status"] == "archived"
    project = offline.get(f"/api/projects/{wf['project_id']}").json()
    assert project["archived"] is True  # NOVA created it for this idea, so it is archived too (reversible)


def test_skip_stage_and_run_all_to_proposal(offline):
    wf = _create(offline, idea="A tool to plan weekly meals for a family.", template="general")
    q = _open_q(wf)
    assert offline.post(f"/api/workflows/{wf['id']}/stages/research/skip").status_code == 409  # clarify first
    offline.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": "Yes, that's right"})
    offline.post(f"/api/workflows/{wf['id']}/clarify/complete")
    wf = offline.post(f"/api/workflows/{wf['id']}/stages/research/skip").json()
    assert next(s for s in wf["stages"] if s["id"] == "research")["note"] == "Skipped by you."
    assert offline.post(f"/api/workflows/{wf['id']}/stages/proposal/skip").status_code == 400
    wf = offline.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True, "all": True},
                      json={"mode": "auto"}).json()
    assert wf["status"] == "awaiting_decision" and wf["proposal"]
    statuses = {s["id"]: s["status"] for s in wf["stages"]}
    assert statuses["research"] == "skipped" and statuses["analysis"] == "done" and statuses["proposal"] == "done"
    assert not wf["running"]


def test_plant_scenario_by_voice_order(offline):
    """The plant scenario: summary + confirmation, then target users, then which plants."""
    wf = _create(offline, idea=PLANT_VOICE, template="auto")
    topics = []
    for answer in ["Yes, that's right", "Home gardeners and smallholders", "Tomatoes first"]:
        q = _open_q(wf)
        topics.append(q["topic"])
        wf = offline.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": answer}).json()
    assert topics == ["intent", "users", "subject_scope"]
    assert _open_q(wf)["topic"] == "classes" and "possible causes" in _open_q(wf)["question"]


def test_existing_workflows_are_not_interrupted_by_the_intent_question(offline):
    # A workflow started before 0.3 has questions but no "intent" question: it continues with its next question.
    wf = _create(offline)
    from app.models import WorkflowQuestion
    with offline.app.state.nova.db.SessionLocal() as s:
        for q in s.query(WorkflowQuestion).filter_by(workflow_id=wf["id"]).all():
            q.topic, q.question = "users", "Who are the primary users?"  # as a 0.2 workflow would have it
        s.commit()
    q = _open_q(offline.get(f"/api/workflows/{wf['id']}").json())
    wf = offline.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", json={"answer": "Gardeners"}).json()
    assert _open_q(wf)["topic"] != "intent"
    assert not any(x["topic"] == "intent" for x in wf["questions"])
