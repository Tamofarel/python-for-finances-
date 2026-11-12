"""Local voice: wake phrase spotting, offline transcription, the streaming protocol and its security.

Audio fixtures are real synthesized speech (Festival HTS voice, see scripts/dev/make_voice_fixtures.sh). They prove
that NOVA's pipeline works with PocketSphinx on this machine; they do not prove accuracy for every speaker or mic.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import AuditEvent
from app.voice import engines
from app.voice.session import Endpointer, VoiceSession
from tests.conftest import make_client, setup_owner

AUDIO = Path(__file__).parent / "fixtures" / "audio"


def pcm(name: str) -> bytes:
    with wave.open(str(AUDIO / f"{name}.wav")) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def run_session(session: VoiceSession, data: bytes, chunk: int = 3200) -> list[dict]:
    out = session.start()
    for i in range(0, len(data), chunk):
        out += session.audio(data[i:i + chunk])
        if session.needs_finish:
            out += session.finish()
    return out


def wake_session(stt: str = "pocketsphinx", phrase: str = "hey nova", sensitivity: str = "medium") -> VoiceSession:
    return VoiceSession(mode="wake", stt=stt, phrase=phrase,
                        make_wake=lambda: engines.PocketSphinxWake(phrase, sensitivity),
                        make_transcriber=None if stt == "browser" else (lambda: engines.make_transcriber(stt, "")))


# ---- engines ---------------------------------------------------------------------------------------------
def test_phrase_validation():
    assert engines.check_phrase("Hey  NOVA")["valid"] is True
    assert engines.check_phrase("hey nova")["message"] is None
    single = engines.check_phrase("nova")
    assert single["valid"] is True and "Single-word" in single["message"]
    unknown = engines.check_phrase("hey zxqvbn")
    assert unknown["valid"] is False and unknown["unknown_words"] == ["zxqvbn"]
    assert engines.check_phrase("hey nova!!")["valid"] is False
    assert engines.check_phrase("one two three four five")["valid"] is False


def test_default_wake_detector_accepts_both_nova_phrases(monkeypatch):
    class Decoder:
        instances = []

        def __init__(self, **_kwargs):
            self.keyphrase = ""
            self.heard = False
            self.instances.append(self)

        def add_keyphrase(self, _name, phrase):
            self.keyphrase = phrase

        def activate_search(self, _name):
            pass

        def start_utt(self):
            pass

        def process_raw(self, _pcm, _no_search, _full_utt):
            pass

        def hyp(self):
            return object() if self.heard else None

        def end_utt(self):
            self.heard = False

    class PocketSphinx:
        pass

    PocketSphinx.Decoder = Decoder

    monkeypatch.setattr(engines, "_pocketsphinx", lambda: PocketSphinx)
    detector = engines.PocketSphinxWake("hey nova")
    assert [decoder.keyphrase for decoder in Decoder.instances] == ["hey nova", "nova"]
    Decoder.instances[1].heard = True
    assert detector.feed(b"\\0\\0") is True
    assert all(not decoder.heard for decoder in Decoder.instances)


def test_short_nova_alias_works_with_real_local_speech_fixture():
    session = wake_session(stt="browser")
    events = run_session(session, pcm("nova_projects"))
    assert any(event["type"] == "wake" for event in events)


def test_strip_wake_words():
    assert engines.strip_wake_words("hey nova what time is it", "hey nova") == "what time is it"
    assert engines.strip_wake_words("nova open my projects", "hey nova") == "open my projects"
    assert engines.strip_wake_words("what time is it", "hey nova") == "what time is it"


def test_engine_statuses_are_honest(tmp_path):
    st = {e.id: e for e in engines.engine_statuses("")}
    assert st["browser"].local is False and "speech service" in st["browser"].note
    assert st["pocketsphinx"].available is True and st["pocketsphinx"].local is True
    assert st["vosk"].available is False and st["vosk"].reason
    st2 = {e.id: e for e in engines.engine_statuses(str(tmp_path / "missing"))}
    assert st2["vosk"].available is False


def test_vosk_model_selection_tracks_english_and_french_preferences():
    settings = Settings(vosk_model_paths="en-US=C:/models/vosk-en|fr-FR=C:/models/vosk-fr")
    assert settings.vosk_model_for("en-US") == "C:/models/vosk-en"
    assert settings.vosk_model_for("fr-CA") == "C:/models/vosk-fr"
    assert settings.vosk_model_for("de-DE") == ""


# ---- endpointing --------------------------------------------------------------------------------------------
def tone(seconds: float, amp: int = 6000, freq: float = 220.0) -> bytes:
    n = int(16000 * seconds)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / 16000))) for i in range(n))


def silence(seconds: float, amp: int = 0) -> bytes:
    return b"".join(struct.pack("<h", amp if i % 2 else -amp) for i in range(int(16000 * seconds)))


def frames(data: bytes):
    for i in range(0, len(data), 960):
        yield data[i:i + 960]


def test_endpointer_detects_speech_and_end():
    ep = Endpointer()
    events = [e for f in frames(silence(0.5, 40) + tone(1.0) + silence(1.2, 40)) if (e := ep.feed_frame(f))]
    assert events[:2] == ["speech_start", "end"]  # the session stops at the first terminal event


def test_endpointer_no_speech_timeout():
    ep = Endpointer(start_timeout=1.0)
    events = [e for f in frames(silence(1.5, 60)) if (e := ep.feed_frame(f))]
    assert events[0] == "no_speech"


def test_endpointer_max_duration():
    ep = Endpointer(max_seconds=1.0)
    events = [e for f in frames(tone(1.5)) if (e := ep.feed_frame(f))]
    assert events[:2] == ["speech_start", "max"]


# ---- sessions with real speech ----------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("hey_nova_weather", "what is the weather like today"),
    ("hey_nova_plants", "i want to build an app"),
])
def test_wake_then_local_transcription(name, expected):
    s = wake_session()
    events = run_session(s, pcm(name))
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["ready", "wake"]
    assert "listening" in kinds and "transcribing" in kinds
    final = [e for e in events if e["type"] == "final"]
    assert final and final[0]["text"].startswith(expected)
    assert not final[0]["text"].startswith("hey nova")  # the wake phrase is removed from the command
    assert s.state == "wake" and s.wakes == 1 and s.utterances == 1


def test_pocketsphinx_wer_on_saved_english_reference_recordings():
    references = {
        "hey_nova_weather": "what is the weather like today",
        "hey_nova_plants": ("i want to build an app that helps people check whether their plants are healthy "
                            "using their phone camera"),
    }
    scores = {}
    for name, reference in references.items():
        session = wake_session()
        events = run_session(session, pcm(name))
        transcript = next(event["text"] for event in events if event["type"] == "final")
        expected, actual = reference.split(), transcript.casefold().split()
        row = list(range(len(actual) + 1))
        for i, word in enumerate(expected, 1):
            current = [i]
            for j, recognized in enumerate(actual, 1):
                current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (word != recognized)))
            row = current
        scores[name] = row[-1] / len(expected)
    assert scores["hey_nova_weather"] == 0.0
    assert scores["hey_nova_plants"] <= 0.10


@pytest.mark.parametrize("name", ["neg_garden", "neg_notes"])
def test_no_false_wake_on_other_speech(name):
    s = wake_session()
    events = run_session(s, pcm(name))
    assert [e["type"] for e in events] == ["ready"]
    assert s.wakes == 0


def test_short_alias_has_known_word_suffix_collision():
    # Local keyphrase spotting can't distinguish “nova” from the suffix in “supernova”.
    events = run_session(wake_session(stt="browser"), pcm("neg_supernova"))
    assert any(event["type"] == "wake" for event in events)


def test_browser_engine_pauses_after_wake_until_resumed():
    s = wake_session(stt="browser")
    events = run_session(s, pcm("hey_nova_weather"))
    assert [e["type"] for e in events] == ["ready", "wake"]
    assert s.state == "paused"
    assert s.audio(pcm("hey_nova_weather")) == []  # audio is dropped while the browser recognises speech
    s.control({"type": "resume"})
    assert s.state == "wake"


def test_utterance_mode_push_to_talk():
    s = VoiceSession(mode="utterance", stt="pocketsphinx", phrase="hey nova", make_wake=None,
                     make_transcriber=lambda: engines.make_transcriber("pocketsphinx", ""))
    events = s.start()
    assert [e["type"] for e in events] == ["ready", "listening"]
    audio = pcm("hey_nova_weather")
    for i in range(0, len(audio), 3200):
        events += s.audio(audio[i:i + 3200])
    assert not s.needs_finish  # push-to-talk ends only when the user releases
    events += s.control({"type": "end"})
    assert s.needs_finish
    events += s.finish()
    final = [e for e in events if e["type"] == "final"]
    assert final and "weather" in final[0]["text"]


def test_cancel_discards_capture():
    s = VoiceSession(mode="utterance", stt="pocketsphinx", phrase="hey nova", make_wake=None,
                     make_transcriber=lambda: engines.make_transcriber("pocketsphinx", ""))
    s.start()
    s.audio(pcm("hey_nova_weather")[:32000])
    assert s.control({"type": "cancel"}) == [{"type": "empty", "reason": "cancelled"}]
    assert s.state == "idle"


def test_local_capture_requires_local_engine():
    s = wake_session(stt="browser")
    s.start()
    out = s.control({"type": "listen"})
    assert out[0]["code"] == "no_local_stt"


# ---- WebSocket protocol and security -------------------------------------------------------------------------
@pytest.fixture
def voice_client(settings, provider):
    c = make_client(settings.model_copy(update={"voice_allow_remote": True}), provider)
    setup_owner(c)
    return c


ORIGIN = {"origin": "http://testserver"}


def hello(c, **kw):
    return {"type": "hello", "csrf": c.headers["X-CSRF-Token"], "mode": "wake", "stt": "pocketsphinx",
            "phrase": "hey nova", "sensitivity": "medium", "sample_rate": 16000, **kw}


def stream(ws, data: bytes, until: str, chunk: int = 3200) -> list[dict]:
    for i in range(0, len(data), chunk):
        ws.send_bytes(data[i:i + chunk])
    events = []
    for _ in range(50):  # bounded: the fixture produces a handful of events
        ev = json.loads(ws.receive_text())
        events.append(ev)
        if ev["type"] == until:
            break
    return events


def test_ws_wake_and_transcript_end_to_end(voice_client):
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(hello(voice_client)))
        assert json.loads(ws.receive_text()) == {"type": "ready", "mode": "wake", "phrase": "hey nova"}
        events = stream(ws, pcm("hey_nova_weather"), until="final")
        kinds = [e["type"] for e in events]
        assert kinds.index("wake") < kinds.index("listening") < kinds.index("transcribing") < kinds.index("final")
        assert events[-1]["text"] == "what is the weather like today"
        ws.send_text(json.dumps({"type": "close"}))
    with voice_client.app.state.nova.db.SessionLocal() as db:
        evs = list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("voice.%"))))
    ended = [e for e in evs if e.action == "voice.stream_ended"]
    assert ended and ended[0].details["wakes"] == 1 and ended[0].details["utterances"] == 1
    assert "weather" not in repr([e.details for e in evs])  # transcripts are never written to the audit log


def test_ws_requires_session_cookie(voice_client):
    voice_client.cookies.clear()
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error" and "Authentication" in msg["message"]


def test_ws_rejects_foreign_origin(voice_client):
    with voice_client.websocket_connect("/api/voice/stream", headers={"origin": "https://evil.example"}) as ws:
        assert "Origin" in json.loads(ws.receive_text())["message"]


def test_ws_rejects_bad_csrf(voice_client):
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(hello(voice_client, csrf="wrong")))
        assert json.loads(ws.receive_text())["code"] == "csrf_failed"


def test_ws_rejects_non_loopback_by_default(settings, provider):
    c = make_client(settings, provider)  # voice_allow_remote is False; TestClient's host is not loopback
    setup_owner(c)
    with c.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        assert "only accepted from this computer" in json.loads(ws.receive_text())["message"]


def test_ws_rejects_unknown_phrase_and_unavailable_engine(voice_client):
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(hello(voice_client, phrase="hey zxqvbn")))
        assert json.loads(ws.receive_text())["code"] == "invalid_wake_phrase"
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(hello(voice_client, stt="vosk")))
        assert json.loads(ws.receive_text())["code"] == "engine_unavailable"


def test_ws_rejects_oversized_frames(voice_client):
    with voice_client.websocket_connect("/api/voice/stream", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(hello(voice_client)))
        ws.receive_text()
        ws.send_bytes(b"\x00" * 40_000)
        assert json.loads(ws.receive_text())["code"] == "frame_too_large"


def test_voice_capabilities_endpoint(voice_client):
    caps = voice_client.get("/api/voice/capabilities").json()
    assert caps["wake"]["available"] is True and caps["wake"]["local"] is True
    ids = {e["id"]: e for e in caps["stt_engines"]}
    assert ids["pocketsphinx"]["available"] and not ids["vosk"]["available"]
    assert voice_client.post("/api/voice/check-phrase", json={"phrase": "nova"}).json()["valid"] is True


def test_vosk_adapter_with_fake_module(monkeypatch, tmp_path):
    """The Vosk adapter is exercised with a fake module (no model is available in the build environment)."""
    import sys
    import types

    class FakeRecognizer:
        def __init__(self, model, rate):
            self.n = 0

        def AcceptWaveform(self, data):  # noqa: N802 - Vosk API name
            self.n += 1
            return False

        def PartialResult(self):  # noqa: N802
            return json.dumps({"partial": "hello"})

        def FinalResult(self):  # noqa: N802
            return json.dumps({"text": "hello nova world"})

    fake = types.SimpleNamespace(SetLogLevel=lambda n: None, Model=lambda p: object(), KaldiRecognizer=FakeRecognizer)
    monkeypatch.setitem(sys.modules, "vosk", fake)
    engines.VoskTranscriber._models.clear()
    tr = engines.make_transcriber("vosk", str(tmp_path))
    assert tr.accept(b"\x00" * 960) == "hello"
    assert tr.result() == "hello nova world"
