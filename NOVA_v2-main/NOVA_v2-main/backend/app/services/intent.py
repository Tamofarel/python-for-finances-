"""Guards and helpers around model-driven actions, each found necessary in live tests with llama3.2 (3B).

1. `looks_like_action`: tools are offered only when the message asks NOVA to *do* something. Questions ("how do I
   open…?", "explain…") are answered without tools; otherwise small models call tools for ordinary questions, e.g.
   opening Wikipedia when asked to explain photosynthesis.
2. `grounded`: a tool call runs only if the request mentions what that tool acts on. llama3.2 answered "turn on the
   kitchen lights" by raising the system volume; this refuses such calls.
3. `resolve_choice`: when NOVA listed options ("1. Cœur De Pirate by Tiakola; 2. …") and the owner answers "the first
   one", the choice is resolved here, exactly, instead of being guessed again by the model.
4. `text_tool_call` / `looks_like_tool_json`: small models sometimes write a tool call as text. A call to a registered
   tool is recovered (and still validated by the registry); imitations of anything else are never shown as a reply.
None of these can start an action on its own; the registry still validates and authorizes every call.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from app.providers.base import ToolCall

_ACTION = re.compile(
    r"\b(play|replay|pause|resume|unpause|stop|skip|next|previous|rewind|volume|louder|quieter|mute|unmute|open|"
    r"launch|start|close|quit|turn|switch|set|lock|unlock|dim|brighten|listen|put on|run|go to|navigate|visit|"
    r"bring up|show me|find|search|summarize|inspect|browse|"
    r"joue|jouer|mets|mettre|lance|lancer|ouvre|ouvrir|ferme|fermer|allume|allumer|eteins|"
    r"eteindre|baisse|baisser|monte|monter|coupe|couper|suivant|precedent|verrouille|deverrouille|ecoute|ecouter)\b")
_QUESTION = re.compile(r"^(how|what|why|when|where|who|which|explain|describe|define|tell me|compare|is|are|does|"
                       r"do|should|can i|pourquoi|comment|quel|quelle|quels|quelles|explique|qu est)\b")
_POLITE = re.compile(r"\b(can|could|would|will) you\b|\bplease\b|\bpeux tu\b|\bpourrais tu\b|\btu peux\b")
_WAKE = re.compile(r"^(hey |ok |dis )?nova\b[\s,:.!]*")

_MEDIA = {"spotify", "music", "song", "songs", "track", "tracks", "playlist", "album", "artist", "play", "pause",
          "resume", "unpause", "stop", "skip", "next", "previous", "rewind", "volume", "louder", "quieter", "mute",
          "unmute", "listen", "musique", "chanson", "morceau", "joue", "mets", "ecoute", "suivant", "precedent", "put",
          "listening", "playing", "playback", "lecture"}
_SOUND = {"sound", "audio", "son", "volume", "mute", "unmute", "louder", "quieter"}
_WEB = {"website", "site", "page", "browser", "url", "link", "web", "navigateur", "localhost", "server", "dev"}
_FILE = {"file", "document", "pdf", "image", "photo", "picture", "fichier", "doc", "open"}
_PROJECT = {"vscode", "vs", "code", "explorer", "folder", "project", "projet", "dossier", "editor"}
_COMMAND = {"test", "tests", "build", "lint", "run", "command", "commande", "pytest", "npm", "check"}
_APP_ALIASES = {"calculator": {"calculator", "calc", "calculatrice"}, "settings": {"settings", "parametres"},
                "notepad": {"notepad", "bloc"}, "file explorer": {"explorer", "explorateur"}}
_HOME_WORDS = {
    "light": {"light", "lights", "lamp", "lamps", "lumiere", "lumieres", "lampe"},
    "lock": {"lock", "unlock", "door", "porte", "verrou"},
    "cover": {"garage", "blind", "blinds", "shutter", "shutters", "curtain", "cover", "volet", "volets", "gate"},
    "climate": {"thermostat", "heating", "heat", "temperature", "ac", "cooling", "chauffage", "clim"},
    "switch": {"switch", "plug", "outlet", "prise"},
    "input_boolean": {"switch", "toggle"},
    "fan": {"fan", "ventilateur"},
    "media_player": {"tv", "television", "speaker", "enceinte"},
    "water_heater": {"water", "heater", "boiler"},
    "valve": {"valve", "water"},
    "siren": {"siren", "alarm"},
    "humidifier": {"humidifier"},
    "scene": {"scene"},
    "sensor": {"temperature", "humidity", "sensor", "capteur"},
    "binary_sensor": {"sensor", "door", "window", "motion"},
}


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold().replace("œ", "oe").replace("æ", "ae"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def _words(text: str) -> set[str]:
    return set(normalize(text).split())


def looks_like_action(text: str) -> bool:
    """True when the message asks NOVA to do something (as opposed to asking about something)."""
    t = _WAKE.sub("", normalize(text))
    if not _ACTION.search(t):
        return False
    return not (_QUESTION.match(t) and not _POLITE.search(t))


def grounded(tool: str, arguments: dict[str, Any], request: str) -> bool:
    """True when the request mentions what this tool would act on."""
    words = _words(request)
    if tool == "spotify_control":
        return bool(words & _MEDIA)
    if tool == "media_control":
        return bool(words & (_MEDIA | _SOUND))
    if tool == "open_app":
        app = normalize(str(arguments.get("app", "")))
        names = _APP_ALIASES.get(app, set()) | set(app.split())
        return bool(app and app in normalize(request)) or bool(words & names)
    if tool == "open_website":
        parts = urlsplit(str(arguments.get("url", "")))
        labels = {lbl for lbl in normalize(parts.hostname or "").split() if len(lbl) >= 3} - {"www", "com", "org", "net"}
        return bool(words & (labels | _WEB)) or bool(parts.port and str(parts.port) in words)
    if tool in ("browser_open", "browser_open_link"):
        if tool == "browser_open":
            parts = urlsplit(str(arguments.get("url", "")))
            labels = {lbl for lbl in normalize(parts.hostname or "").split() if len(lbl) >= 3} - {"www", "com", "org", "net"}
            return bool(words & (labels | _WEB)) or bool(parts.port and str(parts.port) in words)
        return bool(words & {"link", "website", "site", "page", "browser", "url", "web", "lien"})
    if tool in ("browser_inspect", "browser_search"):
        requested = {"page", "website", "site", "browser", "web", "visible", "current", "text", "find", "search",
                     "summarize", "summary"}
        return bool(words & requested)
    if tool == "open_file":
        stem = {w for w in normalize(str(arguments.get("path", ""))).split() if len(w) >= 3}
        return bool(words & (_FILE | stem))
    if tool == "open_project":
        return bool(words & _PROJECT)
    if tool == "run_project_command":
        return bool(words & _COMMAND)
    if tool in ("home_status", "home_control"):
        entity = str(arguments.get("entity_id", ""))
        if not entity:
            return bool(words & set().union(*_HOME_WORDS.values()))
        domain, _, object_id = entity.partition(".")
        names = {w for w in object_id.split("_") if len(w) >= 3}
        return bool(words & names) or bool(words & _HOME_WORDS.get(domain, set()))
    return True  # unknown tools are refused by the registry; nothing to ground here


_SPOTIFY_CONTROLS = {
    "pause": ("pause music", "pause the music", "pause spotify", "pause playback", "pause the song", "pause this song",
              "stop music", "stop the music", "stop spotify", "stop playback", "stop the song",
              "mets en pause", "mets la musique en pause", "arrete la musique"),
    "resume": ("resume music", "resume the music", "resume spotify", "resume playback", "resume the song",
               "continue playback", "continue the music", "unpause", "unpause the music", "play music", "play spotify",
               "reprends la musique", "reprends la lecture"),
    "next": ("next track", "next song", "skip track", "skip song", "skip this song", "skip this track", "skip the song",
             "skip the track", "play the next song", "play the next track", "chanson suivante", "morceau suivant",
             "passe cette chanson"),
    "previous": ("previous track", "previous song", "play the previous song", "play the previous track",
                 "go back to the previous song", "chanson precedente", "morceau precedent"),
    "volume_up": ("increase the volume", "increase volume", "turn up the volume", "turn the volume up", "volume up",
                  "raise the volume", "make it louder", "louder", "monte le volume", "augmente le volume"),
    "volume_down": ("decrease the volume", "decrease volume", "lower the volume", "turn down the volume",
                    "turn the volume down", "volume down", "make it quieter", "quieter", "baisse le volume",
                    "diminue le volume"),
}
_SPOTIFY_PHRASES = {phrase: action for action, phrases in _SPOTIFY_CONTROLS.items() for phrase in phrases}
_NOW_PLAYING = re.compile(
    r"^(?:(?:what|which) (?:song|track|music) is (?:this|that|it|playing|currently playing|on)"
    r"|what(?: s|s| is) (?:playing|currently playing)"
    r"|what(?: s|s| is) (?:this|that|the current) (?:song|track)"
    r"|what am i listening to"
    r"|(?:c est quoi|quelle est) (?:cette|la) (?:chanson|musique)(?: en cours)?"
    r"|quelle (?:chanson|musique) (?:joue|passe))"
    r"(?: right now| now| currently| on spotify| en ce moment| sur spotify)*$")
_PLAY = r"(?:play|put on|joue|mets)"
_ON_SPOTIFY = r"(?: on spotify| sur spotify)?"
_PLAY_PLAYLIST = re.compile(rf"^{_PLAY} (?:my |the |ma |la )?(?:(?P<a>.+?) playlist|playlist (?P<b>.+?)){_ON_SPOTIFY}$")
_PLAY_BY = re.compile(rf"^{_PLAY} (?:the song |the track |la chanson )?(?P<title>.+?) (?:by|par) (?P<artist>.+?){_ON_SPOTIFY}$")
_ANY_TITLE = {"something", "anything", "music", "some music", "a song", "songs", "some songs", "a track"}


def spotify_request(text: str) -> dict[str, Any] | None:
    """spotify_control arguments for an unmistakable Spotify request, or None to let the model decide.

    Covers exact control phrases, "what song is playing?" (a question, so the model would not be offered tools),
    "play <title> by <artist>" and "play my <name> playlist". The registry still validates the call.
    """
    t = _WAKE.sub("", normalize(text))
    t = re.sub(r"^(?:(?:please|can you|could you|would you|peux tu|tu peux) )+", "", t)
    t = re.sub(r"(?: please| s il te plait| for me)+$", "", t).strip()
    if t in _SPOTIFY_PHRASES:
        return {"action": _SPOTIFY_PHRASES[t]}
    if _NOW_PLAYING.match(t):
        return {"action": "now_playing"}
    playlist = _PLAY_PLAYLIST.match(t)
    if playlist:
        return {"action": "play_playlist", "query": (playlist.group("a") or playlist.group("b"))[:240]}
    track = _PLAY_BY.match(t)
    if track:
        title, artist = track.group("title"), track.group("artist")
        if title in _ANY_TITLE:  # "play something by Tiakola": the artist, not a track called "something"
            return {"action": "play_track", "query": artist[:240]}
        # The whole phrase is the query: "by" may belong to the title ("Stand by Me"), and the track matcher uses
        # every word whichever side of "by" it was on.
        return {"action": "play_track", "query": f"{title} by {artist}"[:240]}
    return None


_ORDINALS = [
    {"first", "1", "1st", "premier", "premiere"},
    {"second", "2", "2nd", "deuxieme", "seconde"},
    {"third", "3", "3rd", "troisieme"},
    {"fourth", "4", "4th", "quatrieme"},
    {"fifth", "5", "5th", "cinquieme"},
]
_CARDINALS = {"one": 0, "two": 1, "three": 2, "four": 3, "five": 4, "un": 0, "une": 0, "deux": 1, "trois": 2}
_LAST = {"last", "dernier", "derniere"}
_FILLER = {"the", "one", "please", "play", "it", "that", "this", "song", "track", "number", "by", "le", "la", "les",
           "joue", "mets", "celle", "celui", "ci", "nova", "hey", "ok", "yes", "oui", "a", "an", "playlist"}


def resolve_choice(answer: str, candidates: list[dict[str, Any]]) -> int | None:
    """Which of the offered candidates the owner picked, or None when the answer does not say clearly."""
    words = normalize(answer).split()
    if not words or not candidates:
        return None
    present = set(words)
    if present & _LAST:
        return len(candidates) - 1
    for index, names in enumerate(_ORDINALS):
        if index < len(candidates) and present & names:
            return index
    for i, word in enumerate(words):  # "two" only counts alone or after "number": "the second one" is not "one"
        if word in _CARDINALS and (len(words) == 1 or (i > 0 and words[i - 1] in ("number", "numero", "option"))):
            index = _CARDINALS[word]
            return index if index < len(candidates) else None
    said = present - _FILLER
    hits = []
    for index, c in enumerate(candidates):
        title = set(normalize(str(c.get("name", ""))).split())
        if title and (title <= present or (said and said <= title)):
            hits.append(index)
    return hits[0] if len(hits) == 1 else None


_TOOL_JSON = re.compile(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"(parameters|arguments)"\s*:')


def looks_like_tool_json(text: str) -> bool:
    return bool(_TOOL_JSON.search(text))


def text_tool_call(text: str, tool_names: set[str]) -> ToolCall | None:
    """A registered tool call that a model wrote as JSON text instead of calling it."""
    match = _TOOL_JSON.search(text)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[match.start():])
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("name") not in tool_names:
        return None
    args = value.get("parameters", value.get("arguments", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    return ToolCall("text_call", str(value["name"]), args) if isinstance(args, dict) else None
