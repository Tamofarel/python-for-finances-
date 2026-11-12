"""Guards that keep small local models from acting when nobody asked (found with llama3.2 3B in live probes)."""

from __future__ import annotations

import pytest

from app.services.intent import (
    document_task,
    grounded,
    looks_like_action,
    looks_like_tool_json,
    needs_document,
    resolve_choice,
    spotify_request,
    text_tool_call,
)
from app.tools.windows_agent import SpotifyCommand


@pytest.mark.parametrize("text", [
    "Hey NOVA, open Spotify and play Tiakola — Vie de Pirate.",
    "Can you open spotify and play tiakola vie de pirate",
    "pause the music",
    "open the calculator",
    "turn on the kitchen lights",
    "put on my coding playlist",
    "skip this song",
    "Nova, mets Vie de Pirate de Tiakola",
    "could you run the tests in this project?",
    "unlock the front door please",
    "summarize the visible page",
    "find this phrase on the page",
])
def test_requests_to_act_are_recognised(text):
    assert looks_like_action(text)


@pytest.mark.parametrize("text", [
    "What are the trade-offs between SSE and WebSockets?",
    "Explain how photosynthesis works in two sentences.",
    "How do I open a file in Python?",
    "hi",
    "Review this plan and tell me what could go wrong",
    "Pourquoi le ciel est bleu ?",
])
def test_questions_and_chat_are_not_actions(text):
    assert not looks_like_action(text)


def test_tool_calls_must_be_grounded_in_the_request():
    lights = "turn on the kitchen lights"
    assert not grounded("media_control", {"action": "volume_up"}, lights)
    assert not grounded("spotify_control", {"action": "pause"}, lights)
    assert grounded("home_control", {"entity_id": "light.kitchen", "action": "turn_on"}, lights)
    assert not grounded("home_control", {"entity_id": "lock.front_door", "action": "unlock"}, lights)
    photosynthesis = "Explain how photosynthesis works in two sentences."
    assert not grounded("open_website", {"url": "https://en.wikipedia.org/wiki/Photosynthesis"}, photosynthesis)
    assert grounded("open_website", {"url": "https://github.com/x"}, "open github")
    assert grounded("browser_inspect", {}, "summarize the visible page")
    assert grounded("browser_search", {"query": "title"}, "find this phrase on the page")
    assert grounded("open_app", {"app": "calculator"}, "open the calculator")
    assert not grounded("open_app", {"app": "notepad"}, "open the calculator")
    assert grounded("spotify_control", {"action": "play_track", "query": "Vie de Pirate"},
                    "Hey NOVA, open Spotify and play Tiakola — Vie de Pirate.")
    assert grounded("unknown_tool", {}, "anything")  # unknown tools are refused by the registry instead


def test_spotify_open_with_a_song_means_play_that_song():
    cmd = SpotifyCommand.model_validate({"action": "open", "query": "Tiakola — Vie de Pirate"})
    assert cmd.action == "play_track" and cmd.query == "Tiakola — Vie de Pirate"
    assert SpotifyCommand.model_validate({"action": "play", "query": "Starboy"}).action == "play_track"
    assert SpotifyCommand.model_validate({"action": "stop"}).action == "pause"
    assert SpotifyCommand.model_validate({"action": "open"}).action == "open"
    # Live finding: llama3.2 sent artist="null" (a string), which produced "… by null".
    cmd = SpotifyCommand.model_validate({"action": "play_track", "query": "Vie de Pirate", "artist": "null"})
    assert cmd.artist is None
    assert SpotifyCommand.model_validate({"action": "pause", "query": "None"}).query is None


@pytest.mark.parametrize(("said", "arguments"), [
    ("Hey NOVA, play Bandit by Juice WRLD.", {"action": "play_track", "query": "bandit by juice wrld"}),
    ("Play Stand by Me by Ben E. King", {"action": "play_track", "query": "stand by me by ben e king"}),
    ("play something by Tiakola", {"action": "play_track", "query": "tiakola"}),
    ("Pause the music.", {"action": "pause"}),
    ("Resume playback.", {"action": "resume"}),
    ("Skip this song.", {"action": "next"}),
    ("What song is currently playing?", {"action": "now_playing"}),
    ("What's playing right now?", {"action": "now_playing"}),
    ("Increase the volume.", {"action": "volume_up"}),
    ("Could you turn the volume down please", {"action": "volume_down"}),
    ("Play my Discover Weekly playlist.", {"action": "play_playlist", "query": "discover weekly"}),
    ("Mets la musique en pause", {"action": "pause"}),
    ("C'est quoi cette chanson ?", {"action": "now_playing"}),
])
def test_unmistakable_spotify_requests_map_to_valid_grounded_spotify_calls(said, arguments):
    assert spotify_request(said) == arguments
    assert SpotifyCommand.model_validate(arguments).action == arguments["action"]  # the registry would accept it
    assert grounded("spotify_control", arguments, said)


@pytest.mark.parametrize("said", [
    "What is photosynthesis?", "What is this error about?", "Pause", "Stop", "Resume the workflow",
    "Play", "Open the calculator", "What song should I learn on guitar?", "Turn on the kitchen lights",
    "Stand by for my next message",
])
def test_other_requests_are_left_to_the_model(said):
    assert spotify_request(said) is None


@pytest.mark.parametrize(("said", "task", "document"), [
    ("open microsoft office word  and report what we did today ?", "today_report", True),
    ("Can you open Word and report what we did today?", "today_report", True),
    ("Write a summary of today in a Word document", "today_report", True),
    ("Ouvre Word et fais le rapport de ce qu'on a fait aujourd'hui", "today_report", True),
    ("Open Word and write a letter to my landlord", None, True),  # a document, but the model writes its text
    ("Open Word", None, False),  # opening the app is the whole request
    ("What did we do today?", None, False),  # a question, answered in words
    ("Open the calculator", None, False),
])
def test_requests_for_a_written_document_are_told_apart_from_opening_an_app(said, task, document):
    assert document_task(said) == task
    assert needs_document(said) is document
    if document:
        assert looks_like_action(said) and grounded("word_document", {"action": "create"}, said)


def test_now_playing_takes_no_query_and_common_spellings_are_normalised():
    assert SpotifyCommand.model_validate({"action": "currently_playing"}).action == "now_playing"
    with pytest.raises(ValueError):
        SpotifyCommand.model_validate({"action": "now_playing", "query": "x"})


CHOICES = [{"uri": "spotify:track:coeur", "name": "Cœur De Pirate", "artists": ["Tiakola"]},
           {"uri": "spotify:track:mode", "name": "Mode de vie", "artists": ["Tiakola"]},
           {"uri": "spotify:track:af1", "name": "AF1 NOUVO", "artists": ["Tiakola"]}]


@pytest.mark.parametrize("answer,index", [
    ("the first one", 0), ("1", 0), ("number two", 1), ("le premier", 0), ("the third", 2), ("the last one", 2),
    ("Mode de vie", 1), ("play coeur de pirate please", 0), ("AF1", 2),
])
def test_an_answer_picks_one_of_the_offered_choices(answer, index):
    assert resolve_choice(answer, CHOICES) == index


@pytest.mark.parametrize("answer", ["something else entirely", "Tiakola", "never mind", ""])
def test_unclear_answers_are_left_to_the_model(answer):
    assert resolve_choice(answer, CHOICES) is None


def test_text_imitating_a_tool_call_is_recognised_only_for_registered_tools():
    names = {"spotify_control"}
    call = text_tool_call('{"name": "spotify_control", "parameters": {"action": "pause"}}', names)
    assert call is not None and call.name == "spotify_control" and call.arguments == {"action": "pause"}
    assert text_tool_call('Sure! {"name": "spotify_control", "arguments": {"action": "next"}}', names).arguments == {"action": "next"}
    assert text_tool_call('{"name": "play_track", "parameters": {"track_id": "x"}}', names) is None
    assert text_tool_call("Photosynthesis uses light.", names) is None
    assert looks_like_tool_json('It seems so.\n\n {"name": "play_track", "parameters": {"track_id": "x"}}')
    assert not looks_like_tool_json("Use JSON like {\"a\": 1} in configs.")
