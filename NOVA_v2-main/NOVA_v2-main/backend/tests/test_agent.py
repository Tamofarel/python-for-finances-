"""Model-driven tool orchestration in the conversation pipeline.

The model is the deterministic TEST-ONLY ScriptedProvider: these tests prove NOVA's orchestration (what is offered,
validated, executed, refused, queued, cancelled and reported), not that a live model picks the right tool.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from pydantic import Field
from sqlalchemy import select

from app.models import ActionRequest, AuditEvent
from app.providers.base import ChatMessage, ToolCall
from app.services.agent import MAX_TOOL_CALLS, Agent
from app.tools.registry import PermissionLevel, Tool, ToolInput, ToolRegistry
from tests.conftest import parse_sse


class PlayInput(ToolInput):
    action: str = Field(pattern="^(play_track|pause)$")
    query: str | None = None
    artist: str | None = None


class EmptyInput(ToolInput):
    pass


class Recorder:
    def __init__(self, status="succeeded", message="Playing Vie de Pirate by Tiakola on Spotify (PC).", verified=True):
        self.calls: list[dict] = []
        self.status, self.message, self.verified = status, message, verified

    def __call__(self, args, ctx):
        self.calls.append(args.model_dump(exclude_none=True))
        return self.status, self.message, {"verified": self.verified}


def install(authed, *tools: Tool) -> ToolRegistry:
    registry = ToolRegistry(list(tools))
    authed.app.state.nova.tools = registry
    return registry


def spotify(recorder) -> Tool:
    return Tool("spotify_control", "Control Spotify.", PlayInput, PermissionLevel.ROUTINE, True, recorder)


def send(authed, text="Hey NOVA, open Spotify and play Tiakola — Vie de Pirate."):
    conv = authed.post("/api/conversations", json={"title": "New conversation"}).json()
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    return conv, parse_sse(r.text)


def actions_of(events, phase="finished"):
    return [d for e, d in events if e == "action" and d["phase"] == phase]


def reply_of(events):
    return "".join(d["text"] for e, d in events if e == "delta")


PLAY = ToolCall("c1", "spotify_control", {"action": "play_track", "query": "Vie de Pirate", "artist": "Tiakola"})


def test_supported_request_triggers_the_registered_tool_and_the_reply_uses_its_result(authed, provider):
    recorder = Recorder()
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.turns = [[PLAY], ["Now playing Vie de Pirate by Tiakola."]]
    conv, events = send(authed)

    first = provider.calls[0]
    assert [t.name for t in first["tools"]] == ["spotify_control"]
    assert recorder.calls == [{"action": "play_track", "query": "Vie de Pirate", "artist": "Tiakola"}]
    started, finished = actions_of(events, "started"), actions_of(events)
    assert started[0]["tool"] == "spotify_control" and finished[0]["status"] == "succeeded"
    assert finished[0]["verified"] is True
    # The tool's real result goes back to the model before it answers.
    tool_msg = provider.calls[1]["messages"][-1]
    assert tool_msg.role == "tool" and json.loads(tool_msg.content)["status"] == "succeeded"
    assert reply_of(events) == "Now playing Vie de Pirate by Tiakola."
    saved = authed.get(f"/api/conversations/{conv['id']}").json()["messages"][-1]
    assert saved["actions"][0]["status"] == "succeeded" and saved["status"] == "complete"


def test_explicit_app_alias_uses_direct_route_without_model_round_trip(authed, provider):
    class AppInput(ToolInput):
        app: str = Field(description="Approved application name or alias: microsoft word, word, ms word")

    recorder = Recorder(status="succeeded", message="Opened Microsoft Word.")
    install(authed, Tool("open_app", "Open an approved app.", AppInput, PermissionLevel.ROUTINE, True, recorder))
    _, events = send(authed, "Hey NOVA, open Word.")
    assert recorder.calls == [{"app": "word"}]
    assert not provider.calls
    assert "Opened Microsoft Word." in reply_of(events)


def test_clear_spotify_pause_uses_direct_route_without_model_round_trip(authed, provider):
    recorder = Recorder(status="succeeded", message="Paused Spotify.")
    install(authed, spotify(recorder))
    _, events = send(authed, "Hey NOVA, pause Spotify.")
    assert recorder.calls == [{"action": "pause"}]
    assert not provider.calls
    assert "Paused Spotify." in reply_of(events)


def test_spotify_commands_reach_the_spotify_tool_and_the_reply_is_its_result_not_the_models(authed, provider):
    from app.tools.windows_agent import SpotifyCommand

    said = {
        "Hey NOVA, play Bandit by Juice WRLD.": {"action": "play_track", "query": "bandit by juice wrld"},
        "Pause the music.": {"action": "pause"},
        "Resume playback.": {"action": "resume"},
        "Skip this song.": {"action": "next"},
        "What song is currently playing?": {"action": "now_playing"},  # a question: the model gets no tools for it
        "Increase the volume.": {"action": "volume_up"},
        "Play my Discover Weekly playlist.": {"action": "play_playlist", "query": "discover weekly"},
    }
    unconfirmed = "Spotify accepted the request, but NOVA could not confirm that it is playing."
    recorder = Recorder(status="unverified", message=unconfirmed, verified=False)
    install(authed, Tool("spotify_control", "Control Spotify.", SpotifyCommand, PermissionLevel.ROUTINE, True, recorder))
    provider.replies = ["Done! Your music is playing now."]  # what a model would invent; it must never be used
    for text, arguments in said.items():
        _, events = send(authed, text)
        assert recorder.calls[-1] == arguments, text
        finished = actions_of(events)
        assert [a["status"] for a in finished] == ["unverified"] and finished[0]["verified"] is False
        assert reply_of(events) == unconfirmed, text
    assert len(recorder.calls) == len(said) and not provider.calls


def test_model_is_told_to_act_with_tools_instead_of_describing_them(authed, provider):
    install(authed, spotify(Recorder()))
    provider.tools_supported = True
    provider.turns = [["Which song?"]]
    send(authed, "play some music")
    system = provider.calls[0]["system"]
    assert "call the tool" in system and "instead of" in system
    assert "You cannot take actions yourself" not in system


def test_questions_are_answered_without_offering_tools(authed, provider):
    recorder = Recorder()
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.replies = ["Photosynthesis turns light into chemical energy."]
    _, events = send(authed, "Explain how photosynthesis works in two sentences.")
    assert "tools" not in provider.calls[-1]  # answered by plain streaming
    assert "not a request to act" in provider.calls[-1]["system"]
    assert recorder.calls == [] and actions_of(events) == []


def test_tool_calls_the_owner_did_not_ask_for_are_refused_and_never_run(authed, provider):
    volume = Recorder("unverified", "Sent the volume up media key.", False)
    install(authed, Tool("media_control", "Media keys.", EmptyInput, PermissionLevel.ROUTINE, True, volume))
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "media_control", {})], ["NOVA has no integration for lights."]]
    _, events = send(authed, "turn on the kitchen lights")
    assert volume.calls == [] and actions_of(events) == []
    assert json.loads(provider.calls[1]["messages"][-1].content)["reason"] == "not_requested"
    assert reply_of(events) == "NOVA has no integration for lights."


def test_invalid_arguments_are_rejected_and_never_reach_the_tool(authed, provider):
    recorder = Recorder()
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "spotify_control", {"action": "rm -rf"})], ["That did not work."]]
    _, events = send(authed)
    assert actions_of(events)[0]["status"] == "denied" and recorder.calls == []
    assert "Invalid arguments" in json.loads(provider.calls[1]["messages"][-1].content)["message"]


def test_unknown_tools_are_refused(authed, provider):
    install(authed, spotify(Recorder()))
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "run_shell", {"cmd": "format c:"})], ["I can't do that."]]
    _, events = send(authed)
    assert actions_of(events)[0]["status"] == "denied"


def test_safety_critical_actions_are_queued_for_on_screen_approval_and_run_once(authed, provider):
    unlocked = Recorder(message="Front door is now unlocked.")
    install(authed, Tool("home_control", "Smart home.", EmptyInput, PermissionLevel.APPROVAL, True, unlocked))
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "home_control", {})], ["It is waiting for your approval."]]
    _, events = send(authed, "unlock the front door")
    result = actions_of(events)[0]
    assert result["status"] == "awaiting_approval" and unlocked.calls == []

    pending = authed.get("/api/actions", params={"status": "pending"}).json()
    assert len(pending) == 1 and pending[0]["kind"] == "tool_call" and pending[0]["project_id"] is None
    body = {"params_hash": pending[0]["params_hash"], "confirm": True}
    approved = authed.post(f"/api/actions/{pending[0]['id']}/approve", json=body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["status"] == "succeeded" and len(unlocked.calls) == 1
    assert authed.post(f"/api/actions/{pending[0]['id']}/approve", json=body).status_code == 409
    assert len(unlocked.calls) == 1


def test_tool_failures_are_reported_as_failures(authed, provider):
    install(authed, spotify(Recorder("unavailable", "No Spotify playback device appeared.", False)))
    provider.tools_supported = True
    provider.turns = [[PLAY], [""]]  # the model says nothing: NOVA falls back to the tool's own words
    conv, events = send(authed)
    assert actions_of(events)[0]["status"] == "unavailable"
    assert reply_of(events) == "No Spotify playback device appeared."
    saved = authed.get(f"/api/conversations/{conv['id']}").json()["messages"][-1]
    assert saved["actions"][0]["status"] == "unavailable"


def test_premature_success_claims_before_a_tool_call_are_not_shown(authed, provider):
    install(authed, spotify(Recorder("unverified", "Spotify accepted the request, but NOVA could not confirm it.", False)))
    provider.tools_supported = True
    provider.turns = [["Done! It's playing now. ", PLAY], ["It is playing!"]]
    _, events = send(authed)
    assert "playing" not in reply_of(events).lower()
    assert reply_of(events) == "Spotify accepted the request, but NOVA could not confirm it."


def test_unsuccessful_actions_are_reported_by_nova_not_paraphrased_by_the_model(authed, provider):
    # Live finding: after a refused Spotify call, llama3.2 replied "You need to connect your account. Which account…?"
    message = "Spotify denied this request. Check the account's plan and the permissions granted to NOVA."
    install(authed, spotify(Recorder("failed", message, False)))
    provider.tools_supported = True
    provider.turns = [[PLAY], ["You need to connect to your Spotify account. Which account would you like to use?"]]
    _, events = send(authed)
    assert reply_of(events) == message
    assert len([c for c in provider.calls if "tools" in c]) == 1  # the model is not asked to restate a failure


def test_unsupported_actions_get_an_honest_explanation(authed, provider):
    install(authed)  # no action integrations enabled
    provider.replies = ["NOVA has no integration for that."]
    send(authed, "turn on the kitchen lights")
    system = provider.calls[-1]["system"]
    assert "cannot act" in system and "never pretend" in system


def test_models_without_tool_support_fall_back_with_the_reason(authed, provider):
    install(authed, spotify(Recorder()))
    provider.tools_supported = False
    provider.replies = ["This model can't run actions."]
    _, events = send(authed)
    assert reply_of(events) == "This model can't run actions."
    assert "cannot call tools" in provider.calls[-1]["system"]


def test_runaway_tool_loops_are_bounded(authed, provider):
    recorder = Recorder()
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.turns = [[ToolCall(f"c{i}", "spotify_control", {"action": "pause"})] for i in range(20)]
    _, events = send(authed, "pause forever")
    assert len(recorder.calls) <= MAX_TOOL_CALLS
    assert len(recorder.calls) <= 2  # the same call is not repeated endlessly
    assert "stopped" in reply_of(events).lower()


def test_previous_action_results_are_part_of_the_history(authed, provider):
    install(authed, spotify(Recorder("needs_input", "Which one should I play? 1. A by X; 2. B by Y.", False)))
    provider.tools_supported = True
    provider.turns = [[PLAY], ["Which one?"], ["Playing the first."]]
    conv, _ = send(authed)
    authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "the first one"})
    history = provider.calls[-1]["messages"]
    assert "needs_input" in history[-2].content and "Which one should I play?" in history[-2].content


def test_emergency_stop_pauses_device_control_until_resumed(authed, provider):
    recorder = Recorder()
    install(authed, spotify(recorder), Tool("look", "Observe.", EmptyInput, PermissionLevel.READ, True,
                                            lambda a, c: ("succeeded", "All quiet.", {})))
    csrf = authed.headers.pop("X-CSRF-Token")
    assert authed.post("/api/actions/emergency-stop").status_code == 403
    authed.headers["X-CSRF-Token"] = csrf
    assert authed.post("/api/actions/emergency-stop").json()["paused"] is True
    assert authed.get("/api/actions/control").json()["paused"] is True

    provider.tools_supported = True
    provider.turns = [[PLAY], ["Device control is paused."]]
    _, events = send(authed)
    assert actions_of(events)[0]["status"] == "denied" and recorder.calls == []
    direct = authed.post("/api/tools/execute", json={"name": "spotify_control", "arguments": {"action": "pause"}})
    assert direct.json()["status"] == "denied"
    assert authed.post("/api/tools/execute", json={"name": "look", "arguments": {}}).json()["status"] == "succeeded"

    assert authed.post("/api/actions/resume").json()["paused"] is False
    provider.turns = [[PLAY], ["Playing."]]
    _, events = send(authed)
    assert actions_of(events)[0]["status"] == "succeeded"
    db = authed.app.state.nova.db.SessionLocal()
    try:
        names = [e.action for e in db.scalars(select(AuditEvent))]
    finally:
        db.close()
    assert "actions.emergency_stop" in names and "actions.resumed" in names


def _agent(authed, provider, registry):
    return Agent(authed.app.state.nova, provider=provider, model="scripted-1", owner_id=1, conversation_id=None,
                 project_id=None, workspace_root=None, paused=False, registry=registry)


async def _drain(agent, tools):
    return [ev async for ev in agent.run([ChatMessage("user", "go")], "sys", tools)]


def test_long_running_actions_are_cancelled_by_the_emergency_stop(authed, provider):
    started = threading.Event()

    def slow(_args, ctx):
        started.set()
        ctx.sleep(60)
        return "succeeded", "never", {}

    registry = ToolRegistry([Tool("slow", "Slow.", EmptyInput, PermissionLevel.ROUTINE, True, slow, timeout_s=120)])
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "slow", {})], ["Stopped."]]
    agent = _agent(authed, provider, registry)
    runs = authed.app.state.nova.action_runs

    async def scenario():
        tools, _ = await agent.prepare()
        task = asyncio.ensure_future(_drain(agent, tools))
        for _ in range(250):
            if started.is_set() or task.done():
                break
            await asyncio.sleep(0.02)
        assert started.is_set(), "the tool never started"
        t0 = time.monotonic()
        assert runs.cancel_all(1) == 1
        events = await task
        return events, time.monotonic() - t0

    events, elapsed = asyncio.run(scenario())
    finished = [d for k, d in events if k == "action" and d["phase"] == "finished"]
    assert finished[0]["status"] == "cancelled" and elapsed < 3


def test_actions_have_enforceable_deadlines(authed, provider):
    release = threading.Event()

    def stuck(_args, _ctx):
        release.wait(10)
        return "succeeded", "late", {}

    registry = ToolRegistry([Tool("stuck", "Stuck.", EmptyInput, PermissionLevel.ROUTINE, True, stuck, timeout_s=0.3)])
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "stuck", {})], ["It timed out."]]
    agent = _agent(authed, provider, registry)

    async def scenario():
        tools, _ = await agent.prepare()
        t0 = time.monotonic()
        events = await _drain(agent, tools)
        elapsed = time.monotonic() - t0
        release.set()
        return events, elapsed

    events, elapsed = asyncio.run(scenario())
    finished = [d for k, d in events if k == "action" and d["phase"] == "finished"]
    assert finished[0]["status"] == "timed_out" and elapsed < 3


def test_tool_executions_are_audited_without_their_arguments(authed, provider):
    install(authed, spotify(Recorder()))
    provider.tools_supported = True
    provider.turns = [[PLAY], ["Playing."]]
    send(authed)
    db = authed.app.state.nova.db.SessionLocal()
    try:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "tool.spotify_control"))
        pending = list(db.scalars(select(ActionRequest)))
    finally:
        db.close()
    assert event is not None and event.outcome == "succeeded"
    assert "Vie de Pirate" not in json.dumps(event.details)
    assert pending == []


def test_choosing_from_novas_list_plays_that_exact_item_without_model_guesswork(authed, provider):
    choices = [{"uri": "spotify:track:coeur", "name": "Cœur De Pirate", "artists": ["Tiakola"]},
               {"uri": "spotify:track:mode", "name": "Mode de vie", "artists": ["Tiakola"]}]

    class Chooser(Recorder):
        def __call__(self, args, ctx):
            self.calls.append(args.model_dump(exclude_none=True))
            if not args.uri:
                return "needs_input", "Which one? 1. Cœur De Pirate by Tiakola; 2. Mode de vie by Tiakola.", {
                    "verified": False, "candidates": choices}
            return "succeeded", "Playing Cœur De Pirate by Tiakola on Spotify (PC).", {"verified": True}

    class PlayWithUri(PlayInput):
        uri: str | None = None

    chooser = Chooser()
    install(authed, Tool("spotify_control", "Control Spotify.", PlayWithUri, PermissionLevel.ROUTINE, True, chooser))
    provider.tools_supported = True
    provider.turns = [[PLAY]]
    conv, _ = send(authed)
    model_calls = len(provider.calls)
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "the first one"})
    events = parse_sse(r.text)
    assert chooser.calls[-1]["uri"] == "spotify:track:coeur" and chooser.calls[-1]["artist"] == "Tiakola"
    assert actions_of(events)[0]["status"] == "succeeded"
    assert reply_of(events) == "Playing Cœur De Pirate by Tiakola on Spotify (PC)."
    assert len(provider.calls) == model_calls  # resolved by NOVA, not guessed by the model


def test_text_imitating_a_tool_call_runs_through_the_registry(authed, provider):
    recorder = Recorder("succeeded", "Paused Spotify.", True)
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.turns = [['{"name": "spotify_control", "parameters": {"action": "pause"}}'], ["Paused."]]
    _, events = send(authed, "pause the music")
    assert recorder.calls == [{"action": "pause"}]
    assert '"name"' not in reply_of(events)


def test_imitated_calls_to_unknown_tools_are_never_shown_as_the_reply(authed, provider):
    install(authed, spotify(Recorder()))
    provider.tools_supported = True
    provider.turns = [['Let me try that.\n\n {"name": "play_track", "parameters": {"track_id": "x"}}']]
    _, events = send(authed, "play the first one")
    assert '"name"' not in reply_of(events) and "play_track" not in reply_of(events)
    assert "couldn't" in reply_of(events).lower()


# ---- multi-step tasks: one succeeded action is not the task -----------------------------------------------------
WORD_REQUEST = "Open Microsoft Office Word and report what we did today."


def tasks_of(events):
    return [d for e, d in events if e == "task"]


def install_office(authed, tmp_path, monkeypatch, fake=None, *extra: Tool):
    """The real document and activity tools on a temporary folder, with Windows and Word replaced by fakes."""
    from app.services.activity import activity_tools
    from app.tools.documents import WordDocuments, document_tools
    from tests.test_documents import FakeWindows

    monkeypatch.setattr("app.tools.documents.sys.platform", "win32")
    fake = fake or FakeWindows()
    word = WordDocuments(tmp_path / "NOVA", launcher=fake.launch, processes=fake.processes, windows=fake.windows,
                         resolver=fake.resolve, poll_interval=0.01)
    install(authed, *document_tools(True, word), *activity_tools(lambda: authed.app.state.nova.db.SessionLocal()), *extra)
    return fake


def open_app_tool(recorder) -> Tool:
    class AppInput(ToolInput):
        app: str = Field(description="Approved application name or alias: microsoft word, word")

    return Tool("open_app", "Open an approved app.", AppInput, PermissionLevel.ROUTINE, True, recorder)


def test_word_report_request_is_carried_out_to_a_verified_saved_document(authed, provider, tmp_path, monkeypatch):
    from docx import Document

    fake = install_office(authed, tmp_path, monkeypatch)
    provider.replies = ["Paris is the capital of France."]
    send(authed, "What is the capital of France?")
    _, events = send(authed, WORD_REQUEST)

    finished = actions_of(events)
    assert [(a["tool"], a["arguments"].get("action"), a["status"]) for a in finished] == [
        ("activity_today", None, "succeeded"), ("word_document", "create", "succeeded"),
        ("word_document", "open", "succeeded")]
    assert all(a["completion"] == "ACTION_COMPLETED" and a["verified"] for a in finished)
    # The task is in progress until the last step is verified; succeeded steps alone never complete it.
    statuses = [t["status"] for t in tasks_of(events)]
    assert statuses == ["TASK_IN_PROGRESS", "TASK_IN_PROGRESS", "TASK_IN_PROGRESS", "TASK_COMPLETED"]
    assert [s["state"] for s in tasks_of(events)[1]["steps"]] == ["ACTION_COMPLETED", "PENDING", "PENDING"]

    path = tmp_path / "NOVA" / f"NOVA activity report {time.strftime('%Y-%m-%d')}.docx"
    assert finished[1]["data"]["path"] == str(path) and path.is_file()
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    cells = [c.text for row in Document(str(path)).tables[0].rows for c in row.cells]
    assert "NOVA activity report" in text and "1 request in 1 conversation" in text
    assert "What is the capital of France?" in cells  # the earlier request, from NOVA's records
    assert WORD_REQUEST not in cells  # the request being carried out is not part of the record yet
    assert fake.launched == [str(path)]

    reply = reply_of(events)
    assert str(tmp_path / "NOVA") in reply and "checked that the saved file contains the text" in reply
    assert "Opened" in reply and "in Word" in reply
    assert len(provider.calls) == 1  # only the earlier question: the model writes neither the facts nor the result


def test_document_saved_but_not_seen_in_word_is_a_partially_completed_task(authed, provider, tmp_path, monkeypatch):
    from tests.test_documents import FakeWindows

    install_office(authed, tmp_path, monkeypatch, FakeWindows(installed=False))
    _, events = send(authed, WORD_REQUEST)
    assert [a["status"] for a in actions_of(events)] == ["succeeded", "succeeded", "unavailable"]
    final = tasks_of(events)[-1]
    assert final["status"] == "TASK_PARTIALLY_COMPLETED"
    assert [s["state"] for s in final["steps"]] == ["ACTION_COMPLETED", "ACTION_COMPLETED", "ACTION_FAILED"]
    reply = reply_of(events)
    assert reply.startswith("Task partially completed.") and "Saved the document" in reply
    assert "Not done: show the document in Word." in reply and "not installed" in reply
    assert list((tmp_path / "NOVA").glob("*.docx"))  # what was done is real and stated with its location


def test_failed_step_stops_the_chain_and_later_steps_are_not_attempted(authed, provider, tmp_path, monkeypatch):
    (tmp_path / "NOVA").write_text("a file where the folder should be")
    fake = install_office(authed, tmp_path, monkeypatch)
    _, events = send(authed, WORD_REQUEST)
    finished = actions_of(events)
    assert [(a["tool"], a["status"]) for a in finished] == [("activity_today", "succeeded"), ("word_document", "failed")]
    assert "completion" not in finished[1]
    final = tasks_of(events)[-1]
    assert final["status"] == "TASK_PARTIALLY_COMPLETED"
    assert [s["state"] for s in final["steps"]] == ["ACTION_COMPLETED", "ACTION_FAILED", "NOT_ATTEMPTED"]
    reply = reply_of(events)
    assert "Not done: write and save the Word document." in reply and "could not be saved" in reply
    assert "Not attempted: show the document in Word." in reply
    assert "Opened" not in reply and fake.launched == []  # Word was never opened on a document that does not exist


def test_opening_word_alone_is_a_complete_task_only_when_that_was_the_request(authed, provider, tmp_path, monkeypatch):
    recorder = Recorder(status="succeeded", message="Opened microsoft word.")
    install_office(authed, tmp_path, monkeypatch, None, open_app_tool(recorder))
    _, events = send(authed, "Hey NOVA, open Word.")
    assert recorder.calls == [{"app": "word"}] and tasks_of(events)[-1]["status"] == "TASK_COMPLETED"
    assert actions_of(events)[0]["completion"] == "ACTION_COMPLETED"


def test_model_that_only_opens_word_is_told_the_task_is_not_finished(authed, provider, tmp_path, monkeypatch):
    recorder = Recorder(status="succeeded", message="Opened microsoft word.")
    fake = install_office(authed, tmp_path, monkeypatch, None, open_app_tool(recorder))
    provider.tools_supported = True
    provider.turns = [
        [ToolCall("c1", "open_app", {"app": "word"})],
        ["I opened Word and wrote your letter."],  # the claim the bug produced: nothing was written
        [ToolCall("c2", "word_document", {"action": "create", "title": "Letter to the landlord",
                                          "content": "Dear Sir,\n\nThe heating is broken.\n\n- Since Monday"})],
        ["The letter is saved in your NOVA documents folder and open in Word."],
    ]
    _, events = send(authed, "Open Word and write a letter to my landlord about the heating.")
    assert [(a["tool"], a["status"]) for a in actions_of(events)] == [("open_app", "succeeded"), ("word_document", "succeeded")]
    assert tasks_of(events)[-1]["status"] == "TASK_COMPLETED"
    assert (tmp_path / "NOVA" / "Letter to the landlord.docx").is_file() and len(fake.launched) == 1
    nudges = [m for m in provider.calls[2]["messages"] if m.role == "user" and "task is not finished" in m.content]
    assert len(nudges) == 1 and "word_document" in nudges[0].content  # told once, by NOVA, not left to chance
    reply = reply_of(events)
    assert "wrote your letter" not in reply  # the premature claim was never shown
    assert reply == "The letter is saved in your NOVA documents folder and open in Word."


def test_model_that_never_writes_the_document_cannot_pass_it_off_as_done(authed, provider, tmp_path, monkeypatch):
    recorder = Recorder(status="succeeded", message="Opened microsoft word.")
    install_office(authed, tmp_path, monkeypatch, None, open_app_tool(recorder))
    provider.tools_supported = True
    provider.turns = [[ToolCall("c1", "open_app", {"app": "word"})], ["Done, the letter is written."],
                      ["Here is your letter: Dear Sir..."]]
    _, events = send(authed, "Open Word and write a letter to my landlord about the heating.")
    final = tasks_of(events)[-1]
    assert final["status"] == "TASK_PARTIALLY_COMPLETED"
    assert final["steps"] == [{"step": "open_app", "tool": "open_app", "state": "ACTION_COMPLETED"},
                              {"step": "write and save the Word document", "tool": "word_document", "state": "NOT_ATTEMPTED"}]
    reply = reply_of(events)
    assert "no Word document was created or saved" in reply and "Done, the letter is written." not in reply
    assert not (tmp_path / "NOVA").exists()


def test_today_activity_is_the_owners_record_with_real_results(authed, provider, tmp_path, monkeypatch):
    from datetime import timedelta

    from app.models import Conversation, Message, User, utcnow
    from app.services.activity import today_activity

    recorder = Recorder(status="unverified", message="Spotify accepted the request, but NOVA could not confirm it.",
                        verified=False)
    install(authed, spotify(recorder))
    provider.tools_supported = True
    provider.turns = [[PLAY]]
    send(authed)
    with authed.app.state.nova.db.SessionLocal() as db:
        owner = db.scalar(select(User))
        stranger = User(username="other", display_name="Other", password_hash="x", is_owner=False)
        db.add(stranger)
        db.flush()
        theirs = Conversation(owner_id=stranger.id, title="Private")
        old = Conversation(owner_id=owner.id, title="Yesterday")
        db.add_all([theirs, old])
        db.flush()
        db.add(Message(conversation_id=theirs.id, role="user", content="someone else's secret request"))
        db.add(Message(conversation_id=old.id, role="user", content="an old request",
                       created_at=utcnow() - timedelta(days=2)))
        db.commit()
        found = today_activity(db, owner.id)
    assert found["requests"] == 1 and found["actions"] == 1 and found["unconfirmed"] == 1 and found["succeeded"] == 0
    report = found["report"]
    assert "open Spotify and play Tiakola" in report and "spotify_control: play_track, Vie de Pirate, Tiakola" in report
    assert "Not confirmed. Spotify accepted the request" in report and "## Not completed" in report
    assert "secret" not in report and "an old request" not in report
