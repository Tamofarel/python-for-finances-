"""Local speech engines: wake-phrase spotting and offline transcription.

Everything here runs on this computer. No audio is written to disk or sent anywhere.

- PocketSphinx (pip package `pocketsphinx`, US-English model bundled in the wheel): keyphrase spotting for the wake
  phrase, and basic offline transcription. Low CPU (about 2% of one core while waiting for the wake phrase).
- Vosk (optional, `pip install vosk` plus a model folder in NOVA_VOSK_MODEL_PATH): better offline transcription.

Engines expose honest availability: they report why they cannot run instead of pretending.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
SENSITIVITY_THRESHOLDS = {"low": 1e-12, "medium": 1e-20, "high": 1e-26}
PHRASE_RE = re.compile(r"^[a-z']+( [a-z']+){0,3}$")


@dataclass
class EngineStatus:
    id: str
    label: str
    available: bool
    local: bool
    reason: str | None = None
    note: str | None = None


def _pocketsphinx():  # type: ignore[no-untyped-def]
    if importlib.util.find_spec("pocketsphinx") is None:
        return None
    return importlib.import_module("pocketsphinx")


def normalize_phrase(phrase: str) -> str:
    return " ".join(phrase.lower().replace("’", "'").split())


# ---- wake phrase --------------------------------------------------------------------------------------
class WakeDetector(ABC):
    @abstractmethod
    def feed(self, pcm: bytes) -> bool:
        """Process 16 kHz mono PCM16 audio. Returns True when the wake phrase was just heard."""

    @abstractmethod
    def reset(self) -> None: ...


class PocketSphinxWake(WakeDetector):
    def __init__(self, phrase: str, sensitivity: str = "medium") -> None:
        ps = _pocketsphinx()
        if ps is None:
            raise RuntimeError("PocketSphinx is not installed (pip install pocketsphinx).")
        self.phrase = normalize_phrase(phrase)
        # Keep the convenient short alias available with NOVA's default wake phrase.
        # Separate KWS decoders let PocketSphinx listen for either phrase locally.
        self.phrases = [self.phrase, "nova"] if self.phrase == "hey nova" else [self.phrase]
        threshold = SENSITIVITY_THRESHOLDS.get(sensitivity, SENSITIVITY_THRESHOLDS["medium"])
        self._decoders = []
        for index, wake_phrase in enumerate(self.phrases):
            # The one-word alias is inherently more ambiguous. Keep it at the
            # detector's low sensitivity even when the two-word phrase is tuned up.
            phrase_threshold = 1e-6 if len(wake_phrase.split()) == 1 else threshold
            decoder = ps.Decoder(samprate=SAMPLE_RATE, kws_threshold=phrase_threshold, loglevel="FATAL")
            decoder.add_keyphrase(f"wake_{index}", wake_phrase)
            decoder.activate_search(f"wake_{index}")
            decoder.start_utt()
            self._decoders.append(decoder)

    def feed(self, pcm: bytes) -> bool:
        detected = False
        for decoder in self._decoders:
            decoder.process_raw(pcm, False, False)
            detected = detected or decoder.hyp() is not None
        if detected:
            self.reset()
            return True
        return False

    def reset(self) -> None:
        for decoder in self._decoders:
            decoder.end_utt()
            decoder.start_utt()


def check_phrase(phrase: str) -> dict[str, object]:
    """Validate a wake phrase against the local pronunciation dictionary."""
    norm = normalize_phrase(phrase)
    if not PHRASE_RE.match(norm):
        return {"phrase": norm, "valid": False, "unknown_words": [],
                "message": "Use 1 to 4 words with letters only, for example 'hey nova'."}
    ps = _pocketsphinx()
    if ps is None:
        return {"phrase": norm, "valid": False, "unknown_words": [], "message": "PocketSphinx is not installed."}
    d = ps.Decoder(samprate=SAMPLE_RATE, loglevel="FATAL")
    unknown = [w for w in norm.split() if d.lookup_word(w) is None]
    if unknown:
        return {"phrase": norm, "valid": False, "unknown_words": unknown,
                "message": "These words are not in the local English dictionary: " + ", ".join(unknown)}
    warning = None
    if len(norm.split()) == 1:
        warning = "Single-word phrases trigger more often by mistake. Two words such as 'hey nova' work better."
    return {"phrase": norm, "valid": True, "unknown_words": [], "message": warning}


# ---- transcription ----------------------------------------------------------------------------------
class Transcriber(ABC):
    @abstractmethod
    def accept(self, pcm: bytes) -> str | None:
        """Feed audio; may return a partial transcript."""

    @abstractmethod
    def result(self) -> str:
        """Finish and return the final transcript ('' when nothing was recognised)."""


class PocketSphinxTranscriber(Transcriber):
    def __init__(self) -> None:
        ps = _pocketsphinx()
        if ps is None:
            raise RuntimeError("PocketSphinx is not installed.")
        self._d = ps.Decoder(samprate=SAMPLE_RATE, loglevel="FATAL")
        self._d.start_utt()

    def accept(self, pcm: bytes) -> str | None:
        self._d.process_raw(pcm, False, False)
        return None  # partial hypotheses from PocketSphinx are too unstable to show

    def result(self) -> str:
        self._d.end_utt()
        hyp = self._d.hyp()
        return hyp.hypstr if hyp is not None else ""


class VoskTranscriber(Transcriber):
    _models: dict[str, object] = {}

    def __init__(self, model_path: str) -> None:
        vosk = importlib.import_module("vosk")
        vosk.SetLogLevel(-1)
        model = self._models.get(model_path)
        if model is None:
            model = vosk.Model(model_path)
            self._models[model_path] = model
        self._r = vosk.KaldiRecognizer(model, SAMPLE_RATE)

    def accept(self, pcm: bytes) -> str | None:
        if self._r.AcceptWaveform(pcm):
            return json.loads(self._r.Result()).get("text") or None
        return json.loads(self._r.PartialResult()).get("partial") or None

    def result(self) -> str:
        return str(json.loads(self._r.FinalResult()).get("text", "")).strip()


def strip_wake_words(text: str, phrase: str) -> str:
    """Remove the wake phrase (or its first/last word) from the start of a transcript."""
    words = text.split()
    phrase_words = normalize_phrase(phrase).split()
    for cut in (phrase_words, phrase_words[-1:], phrase_words[:1]):
        if words[: len(cut)] == cut:
            words = words[len(cut):]
            break
    return " ".join(words)


# ---- availability ----------------------------------------------------------------------------------
def engine_statuses(vosk_model_path: str) -> list[EngineStatus]:
    ps = _pocketsphinx() is not None
    out = [
        EngineStatus("browser", "Browser speech recognition", True, local=False,
                     note="Chrome and Edge send the audio to the browser vendor's speech service while you talk. "
                          "Not available in Firefox."),
        EngineStatus("pocketsphinx", "PocketSphinx (offline, basic)", ps, local=True,
                     reason=None if ps else "pip install pocketsphinx",
                     note="Runs on this computer. US-English only; accuracy is limited, best for short sentences."),
    ]
    vosk_ok, reason = False, None
    if importlib.util.find_spec("vosk") is None:
        reason = "Optional: pip install vosk and download a model (see docs/VOICE.md)."
    elif not vosk_model_path:
        reason = "Set NOVA_VOSK_MODEL_PATH to an unpacked Vosk model folder."
    elif not (Path(vosk_model_path) / "conf").is_dir() and not (Path(vosk_model_path) / "am").is_dir():
        reason = f"No Vosk model found at {vosk_model_path}."
    else:
        vosk_ok = True
    out.append(EngineStatus("vosk", "Vosk (offline, better accuracy)", vosk_ok, local=True, reason=reason,
                            note="Runs on this computer. Models exist for English, French and other languages."))
    return out


def wake_status() -> EngineStatus:
    ok = _pocketsphinx() is not None
    return EngineStatus("pocketsphinx-kws", "Local wake phrase (PocketSphinx)", ok, local=True,
                        reason=None if ok else "pip install pocketsphinx",
                        note="Listens on this computer only. English pronunciation dictionary.")


def make_transcriber(engine: str, vosk_model_path: str) -> Transcriber:
    if engine == "vosk":
        return VoskTranscriber(vosk_model_path)
    return PocketSphinxTranscriber()
