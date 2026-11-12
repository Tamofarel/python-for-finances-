"""Voice session logic, independent of the transport so it can be tested with plain audio bytes.

Modes
- "wake": spot the wake phrase. After a hit, either capture the following utterance and transcribe it locally
  (local engines), or pause until the client says it is done with its own (browser) recognition.
- "utterance": capture one utterance right away (push-to-talk or a follow-up question).

The session never stores audio beyond the current utterance buffer, which is discarded after transcription.
"""

from __future__ import annotations

import array
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.voice.engines import SAMPLE_RATE, Transcriber, WakeDetector, strip_wake_words

FRAME_BYTES = 960  # 30 ms of 16 kHz PCM16
PRE_ROLL_SECONDS = 0.6
log = get_logger("nova.voice")


def rms(pcm: bytes) -> float:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a))


@dataclass
class Endpointer:
    """Energy-based speech endpoint detection with an adaptive noise floor."""

    start_timeout: float = 5.0  # seconds to wait for speech before giving up
    silence_after: float = 0.8  # trailing silence that ends an utterance
    max_seconds: float = 20.0
    min_speech_level: float = 300.0  # ~ -40 dBFS
    noise: float = 50.0
    elapsed: float = 0.0
    speech: bool = False
    speech_seconds: float = 0.0
    silence: float = 0.0

    def feed_frame(self, frame: bytes) -> str | None:
        """Returns 'speech_start', 'end', 'no_speech' or 'max' when something happens."""
        dur = len(frame) / 2 / SAMPLE_RATE
        self.elapsed += dur
        level = rms(frame)
        threshold = max(self.min_speech_level, self.noise * 3.0)
        voiced = level > threshold
        if not voiced:
            self.noise = 0.95 * self.noise + 0.05 * level  # adapt only on non-speech frames
        if voiced:
            self.silence = 0.0
            self.speech_seconds += dur
            if not self.speech and self.speech_seconds >= 0.09:
                self.speech = True
                return "speech_start"
        else:
            self.silence += dur
            if not self.speech:
                self.speech_seconds = 0.0
        if self.speech and self.silence >= self.silence_after:
            return "end"
        if not self.speech and self.elapsed >= self.start_timeout:
            return "no_speech"
        if self.elapsed >= self.max_seconds:
            return "max"
        return None


@dataclass
class VoiceSession:
    mode: str  # wake | utterance
    stt: str  # browser | pocketsphinx | vosk
    phrase: str
    make_wake: Callable[[], WakeDetector] | None
    make_transcriber: Callable[[], Transcriber] | None
    max_seconds: float = 20.0
    state: str = "idle"  # wake | capturing | finishing | paused | idle | closed
    wakes: int = 0
    utterances: int = 0
    _wake: WakeDetector | None = None
    _tr: Transcriber | None = None
    _ep: Endpointer | None = None
    _pending: bytearray = field(default_factory=bytearray)
    _pre: deque[bytes] = field(default_factory=lambda: deque(maxlen=int(PRE_ROLL_SECONDS / 0.03)))
    _wake_initiated: bool = False
    _ptt: bool = False
    _wake_started_at: float = 0.0
    _speech_started_at: float | None = None

    # ---- lifecycle ------------------------------------------------------------------------------------
    def start(self) -> list[dict[str, Any]]:
        if self.mode == "wake":
            if self.make_wake is None:
                raise RuntimeError("Wake detection is not available.")
            self._wake = self.make_wake()
            self.state = "wake"
            self._wake_started_at = time.monotonic()
            log.info("voice wake detector ready", extra={"extra_data": {"engine": "pocketsphinx"}})
            return [{"type": "ready", "mode": "wake", "phrase": self.phrase}]
        self.state = "idle"
        events = [{"type": "ready", "mode": "utterance"}]
        events += self._begin_capture(ptt=True, wake_initiated=False)
        return events

    def control(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        kind = msg.get("type")
        if kind == "pause":
            self._discard()
            self.state = "paused"
            return []
        if kind == "resume":
            if self.mode == "wake":
                self._discard()
                if self._wake:
                    self._wake.reset()
                self.state = "wake"
            return []
        if kind == "listen":  # capture one utterance now (follow-up or push-to-talk)
            return self._begin_capture(ptt=bool(msg.get("ptt")), wake_initiated=False)
        if kind == "end":
            return self._request_finish() if self.state == "capturing" else []
        if kind == "cancel":
            self._discard()
            self.state = "wake" if self.mode == "wake" else "idle"
            return [{"type": "empty", "reason": "cancelled"}]
        return [{"type": "error", "code": "bad_message", "message": f"Unknown message type: {kind!r}"}]

    def close(self) -> None:
        self._discard()
        self.state = "closed"

    # ---- audio ------------------------------------------------------------------------------------------
    def audio(self, pcm: bytes) -> list[dict[str, Any]]:
        self._pending.extend(pcm)
        out: list[dict[str, Any]] = []
        while len(self._pending) >= FRAME_BYTES and not self.needs_finish:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            out += self._frame(frame)
        return out

    def _frame(self, frame: bytes) -> list[dict[str, Any]]:
        if self.state == "wake":
            self._pre.append(frame)
            assert self._wake is not None
            detector_started = time.monotonic()
            if self._wake.feed(frame):
                self.wakes += 1
                log.info("voice wake detected", extra={"extra_data": {
                    "detector_ms": round((time.monotonic() - detector_started) * 1000),
                    "session_ms": round((time.monotonic() - self._wake_started_at) * 1000)}})
                events: list[dict[str, Any]] = [{"type": "wake", "phrase": self.phrase}]
                if self.stt == "browser":
                    self.state = "paused"  # the browser runs its own recognition; client resumes us
                else:
                    pre = list(self._pre)
                    events += self._begin_capture(ptt=False, wake_initiated=True)
                    for f in pre[-int(0.3 / 0.03):]:  # short pre-roll in case speech continued
                        if self._tr:
                            self._tr.accept(f)
                return events
            return []
        if self.state == "capturing":
            assert self._tr is not None and self._ep is not None
            partial = self._tr.accept(frame)
            out: list[dict[str, Any]] = []
            if partial:
                out.append({"type": "partial", "text": strip_wake_words(partial, self.phrase)})
            ev = self._ep.feed_frame(frame)
            if ev == "speech_start":
                self._speech_started_at = time.monotonic()
                out.append({"type": "speech"})
            elif ev == "no_speech" and not self._ptt:
                self._discard()
                self.state = "wake" if self.mode == "wake" else "idle"
                out.append({"type": "empty", "reason": "no_speech"})
            elif ev == "end" and not self._ptt:
                out += self._request_finish()
            elif ev == "max":
                out += self._request_finish()
            return out
        return []  # paused / idle: audio is dropped

    # ---- capture ----------------------------------------------------------------------------------------
    def _begin_capture(self, *, ptt: bool, wake_initiated: bool) -> list[dict[str, Any]]:
        if self.stt == "browser" or self.make_transcriber is None:
            return [{"type": "error", "code": "no_local_stt",
                     "message": "Local transcription is not enabled. Choose a local engine in Settings → Voice."}]
        self._tr = self.make_transcriber()
        self._ep = Endpointer(start_timeout=6.0 if wake_initiated else 8.0, max_seconds=self.max_seconds)
        self._ptt = ptt
        self._wake_initiated = wake_initiated
        self._speech_started_at = None
        log.info("voice capture started", extra={"extra_data": {"engine": self.stt, "ptt": ptt}})
        self.state = "capturing"
        return [{"type": "listening"}]

    @property
    def needs_finish(self) -> bool:
        return self.state == "finishing"

    def _request_finish(self) -> list[dict[str, Any]]:
        """Stop capturing. The transport then calls finish() (possibly in a worker thread)."""
        if self._ep is not None:
            log.info("voice end of speech", extra={"extra_data": {
                "utterance_ms": round(self._ep.elapsed * 1000), "silence_ms": round(self._ep.silence * 1000),
                "speech_ms": round(self._ep.speech_seconds * 1000),
                "speech_to_endpoint_ms": round((time.monotonic() - self._speech_started_at) * 1000)
                if self._speech_started_at is not None else None}})
        self.state = "finishing"
        return [{"type": "transcribing"}]

    def finish(self) -> list[dict[str, Any]]:
        """Decode the captured utterance. Audio is discarded afterwards."""
        if self._tr is None:
            return []
        started = time.monotonic()
        text = self._tr.result()
        log.info("voice transcription finished", extra={"extra_data": {
            "engine": self.stt, "duration_ms": round((time.monotonic() - started) * 1000),
            "recognized": bool(text.strip())}})
        if self._wake_initiated:
            text = strip_wake_words(text, self.phrase)
        self._discard()
        self.state = "wake" if self.mode == "wake" else "idle"
        if self._wake:
            self._wake.reset()
        if not text.strip():
            return [{"type": "empty", "reason": "not_recognised"}]
        self.utterances += 1
        return [{"type": "final", "text": text.strip()}]

    def _discard(self) -> None:
        self._tr = None
        self._ep = None
        self._ptt = False
        self._speech_started_at = None
        self._pending.clear()
