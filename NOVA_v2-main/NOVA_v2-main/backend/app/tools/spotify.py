"""Spotify OAuth PKCE, DPAPI token storage, and user-authorized Web API playback.

Playback is reported as successful only after Spotify's own player state shows the requested item playing. Every
other outcome is reported precisely: not connected, no playback device, Premium required, ambiguous search, rate
limited, or accepted-but-unconfirmed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.logging import redact, redact_text
from app.tools import desktop_os
from app.tools.registry import ToolContext

AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"  # noqa: S105 - OAuth endpoint, not a credential
API = "https://api.spotify.com/v1"
log = logging.getLogger("nova.spotify")
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-private "
         "playlist-read-private playlist-read-collaborative")
CONNECT_URL = "/api/spotify/connect"
DEVICE_WAIT_SECONDS = 12.0  # the desktop app needs a few seconds to register as a playback device
TRANSFER_WAIT_SECONDS = 8.0  # an idle device took about 6 s to report itself active after a transfer (live test)
VERIFY_SECONDS = 8.0  # per play request: Spotify's reported player state may lag the command by several seconds
CONTROL_ACTIONS = ("pause", "resume", "play_pause", "next", "previous", "volume_up", "volume_down")
APP_ACTIONS = ("open", "search")  # these start the desktop app; everything else goes through the Web API only
PLAYBACK_SCOPES = frozenset({"user-read-playback-state", "user-modify-playback-state"})
NO_LAUNCH = ("Opening the Spotify app needs desktop control, which is off (NOVA_DESKTOP_CONTROL_ENABLED=false). "
             "Playback through your Spotify account still works: open Spotify yourself, then ask again.")
# Machine-readable outcome of a Web API operation, in data["outcome"]. SUCCESS is only ever set after Spotify's own
# player state confirmed the result.
_BLOCKER_OUTCOMES = {"no_device": "NO_DEVICE", "device_restricted": "DEVICE_UNAVAILABLE",
                     "not_connected": "AUTHENTICATION_REQUIRED", "expired": "AUTHENTICATION_REQUIRED",
                     "unauthorized": "AUTHENTICATION_REQUIRED", "insufficient_scope": "INSUFFICIENT_SCOPE"}
_STATUS_OUTCOMES = {"succeeded": "SUCCESS", "unverified": "REQUEST_ACCEPTED", "failed": "PLAYBACK_FAILED"}


class SpotifyProblem(Exception):
    """Safe, user-facing integration failure."""


class DpapiTokenStore:
    """Protect tokens with Windows DPAPI for the current user; never write plaintext token files."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _crypt(data: bytes, protect: bool) -> bytes:
        if sys.platform != "win32":
            raise SpotifyProblem("Secure Spotify token storage is available only on Windows in this build.")
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
        output = Blob()
        crypt = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        ok = crypt(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
        if not ok:
            raise SpotifyProblem("Windows could not protect the Spotify token for this account.")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def save(self, tokens: dict[str, object]) -> None:
        protected = self._crypt(json.dumps(tokens, separators=(",", ":")).encode("utf-8"), True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(protected)
        os.replace(temporary, self.path)

    def load(self) -> dict[str, object] | None:
        if not self.path.is_file():
            return None
        try:
            plain = self._crypt(self.path.read_bytes(), False)
            value = json.loads(plain)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, json.JSONDecodeError, SpotifyProblem):
            return None

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass
class PendingOAuth:
    owner_id: int
    verifier: str
    expires_at: float


class SpotifyService:
    def __init__(self, client_id: str, port: int, token_path: Path, *, transport: httpx.BaseTransport | None = None,
                 launcher: Callable[[str], bool] | None = None, processes: Callable[[], set[str]] | None = None,
                 poll_interval: float = 0.5) -> None:
        self.client_id = client_id.strip()
        self.launcher = launcher or desktop_os.launch
        self.processes = processes or desktop_os.running_processes
        self.poll_interval = poll_interval
        self.redirect_uri = f"http://127.0.0.1:{port}/api/spotify/callback"
        self.tokens = DpapiTokenStore(token_path)
        self.pending: dict[str, PendingOAuth] = {}
        self.transport = transport
        self.client = httpx.Client(transport=transport, follow_redirects=False)

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    def begin(self, owner_id: int) -> str:
        if not self.configured:
            raise SpotifyProblem("Set NOVA_SPOTIFY_CLIENT_ID and configure the callback URI in Spotify Developer settings.")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        self.pending[state] = PendingOAuth(owner_id, verifier, time.time() + 600)
        for key, item in list(self.pending.items()):
            if item.expires_at < time.time():
                self.pending.pop(key, None)
        return (
            AUTH
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "response_type": "code",
                    "redirect_uri": self.redirect_uri,
                    "scope": SCOPES,
                    "state": state,
                    "code_challenge_method": "S256",
                    "code_challenge": challenge,
                }
            )
        )

    def finish(self, code: str, state: str) -> int:
        pending = self.pending.pop(state, None)
        if pending is None or pending.expires_at < time.time():
            raise SpotifyProblem("Spotify authorization expired or its state did not match. Connect again.")
        try:
            response = self._http().post(
                TOKEN,
                data={
                    "client_id": self.client_id,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": pending.verifier,
                },
                timeout=12,
            )
        except httpx.HTTPError as exc:
            message = "Spotify authorization could not be reached. Check your internet connection and try again."
            raise SpotifyProblem(message) from exc
        if response.status_code != 200:
            raise SpotifyProblem("Spotify could not complete authorization. Check the callback URL and try again.")
        body = response.json()
        if not body.get("access_token") or not body.get("refresh_token"):
            raise SpotifyProblem("Spotify returned an incomplete authorization response.")
        self.tokens.save(
            {
                "access_token": body["access_token"],
                "refresh_token": body["refresh_token"],
                "expires_at": time.time() + int(body.get("expires_in", 3600)),
                "scope": body.get("scope", ""),
            }
        )
        return pending.owner_id

    def status(self) -> dict[str, object]:
        token = self.tokens.load() if sys.platform == "win32" else None
        return {
            "configured": self.configured,
            "connected": bool(token),
            "reason": None if self.configured else "NOVA_SPOTIFY_CLIENT_ID is not configured.",
        }

    def disconnect(self) -> None:
        self.tokens.delete()

    def close(self) -> None:
        self.client.close()

    def _http(self) -> httpx.Client:
        return self.client

    # ---- commands -----------------------------------------------------------------------------------------
    def command(self, action: str, query: str | None = None, *, artist: str | None = None, uri: str | None = None,
                ctx: ToolContext | None = None, allow_launch: bool = True) -> tuple[str, str, dict[str, Any]]:
        """Run one Spotify operation. Raises ToolCancelled/ToolDeadline when stopped; everything else is a result.

        allow_launch=False keeps this to the Spotify Web API: the desktop app is never started (desktop control off).
        """
        status, message, data = self._command(action, query, artist, uri,
                                              ctx or ToolContext(deadline=time.monotonic() + 30), allow_launch)
        if action not in APP_ACTIONS and "outcome" not in data:
            outcome = _BLOCKER_OUTCOMES.get(str(data.get("blocker"))) or _STATUS_OUTCOMES.get(status)
            if outcome:
                data = {**data, "outcome": outcome}
        return status, message, data

    def _command(self, action: str, query: str | None, artist: str | None, uri: str | None, ctx: ToolContext,
                 allow_launch: bool) -> tuple[str, str, dict[str, Any]]:
        if not self.configured:
            return ("unavailable", "Spotify is not configured: add your Spotify app's client ID as NOVA_SPOTIFY_CLIENT_ID "
                    "in .env and restart NOVA.", {"verified": False, "blocker": "not_configured"})
        if sys.platform != "win32":
            return "unavailable", "Spotify control is only available on Windows in this build.", {"verified": False}
        try:
            if action in APP_ACTIONS and not allow_launch:
                return "unavailable", NO_LAUNCH, {"verified": False, "blocker": "desktop_control_off"}
            if action == "open":
                return self._open(ctx)
            if action == "search":
                return self._open_search(query or "")
            token = self._access_token(ctx)
            if action == "now_playing":
                return self._now_playing(token, ctx)
            if action in ("play_track", "play_playlist") and uri:
                return self._play_uri(token, uri, f"{query} by {artist}" if query and artist else query, ctx,
                                      allow_launch)
            if action == "play_track":
                return self._play_track(token, query or "", artist, ctx, allow_launch)
            if action == "play_playlist":
                return self._play_playlist(token, query or "", ctx, allow_launch)
            if action in CONTROL_ACTIONS:
                return self._control(token, action, ctx)
        except _Blocked as blocked:
            return blocked.status, blocked.message, {"verified": False, **blocked.data}
        return "unavailable", "Spotify does not support that operation.", {"verified": False}

    # ---- app ------------------------------------------------------------------------------------------------
    def _open(self, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        if not self.launcher("spotify:"):
            return ("unavailable", "Windows could not open Spotify. Install the Spotify desktop app and try again.",
                    {"verified": False, "blocker": "not_installed"})
        until = min(ctx.deadline - 0.2, time.monotonic() + 8)
        while True:
            if "spotify.exe" in self.processes():
                return "succeeded", "Opened Spotify.", {"verified": True}
            if time.monotonic() + 0.5 >= until:
                return ("unverified", "Asked Windows to open Spotify, but NOVA could not see the app start.",
                        {"verified": False})
            ctx.sleep(0.5)

    def _open_search(self, query: str) -> tuple[str, str, dict[str, Any]]:
        if not self.launcher(f"spotify:search:{quote(query, safe='')}"):
            return "unavailable", "Windows could not open Spotify. Install the Spotify desktop app and try again.", {}
        return ("unverified", f"Opened Spotify search for “{query}”. Choose a result there to play it.",
                {"verified": False, "operation": "search_opened"})

    # ---- playback -------------------------------------------------------------------------------------------
    def _play_track(self, token: str, query: str, artist: str | None, ctx: ToolContext,
                    allow_launch: bool = True) -> tuple[str, str, dict[str, Any]]:
        # Plain text, no artist: filter: models sometimes swap title and artist, and the matcher below uses every word.
        q = _clean(f"{query} {artist or ''}")
        asked = f"{query} by {artist}" if artist else query
        found = _json(self._request(token, "GET", "/search", ctx,
                                    params={"q": q, "type": "track", "limit": 10, "market": "from_token"}))
        results = [t for t in found.get("tracks", {}).get("items", []) if isinstance(t, dict) and t.get("uri")]
        items = [t for t in results if t.get("is_playable") is not False and not t.get("restrictions")]
        if not items:
            if results:
                raise _Blocked("unavailable", f"Spotify found {asked}, but it is not playable in your account's market. "
                               "Try another version of the track.", {"blocker": "market_unavailable"})
            raise _Blocked("unavailable", f"Spotify found no track for “{asked}”. Try another title or artist.",
                           {"blocker": "not_found"})
        wanted = _tokens(f"{query} {artist or ''}")
        track = _best_track(items, wanted)
        if track is not None:
            chosen = track
            return self._start(token, ctx, _track_bodies(chosen), _track_label(chosen), chosen["uri"],
                               lambda s: _same_track(s.get("item"), chosen), {"track": _summary(chosen)}, allow_launch)
        artist_hit = _artist_request(items, wanted)
        if artist_hit is not None:
            uri, name = artist_hit
            return self._start(token, ctx, [{"context_uri": uri}], f"music by {name}", uri,
                               lambda s: (s.get("context") or {}).get("uri") == uri
                               or any(a.get("uri") == uri for a in (s.get("item") or {}).get("artists", [])),
                               {"artist": name}, allow_launch)
        choices = items[:3]
        listing = "; ".join(f"{i}. {_track_label(t)}" for i, t in enumerate(choices, 1))
        raise _Blocked("needs_input", f"I'm not sure which track you mean by “{asked}”. Spotify's top results: {listing}. "
                       "Which one should I play?", {"candidates": [_summary(t) for t in choices]})

    def _play_uri(self, token: str, uri: str, name: str | None, ctx: ToolContext,
                  allow_launch: bool = True) -> tuple[str, str, dict[str, Any]]:
        """Play an exact item NOVA listed earlier (the owner chose it), without searching again."""
        label = name or "the item you chose"
        if uri.startswith("spotify:track:"):
            track: dict[str, Any] = {"uri": uri, "id": uri.rsplit(":", 1)[1], "name": label}
            # The track's album is needed to start it inside its album (see _track_bodies); a failed lookup only
            # means the plain request is used.
            found = self._request(token, "GET", f"/tracks/{track['id']}", ctx, params={"market": "from_token"},
                                  allow=(400, 404))
            album = _json(found).get("album") if found.status_code == 200 else None
            if isinstance(album, dict):
                track["album"] = album
            return self._start(token, ctx, _track_bodies(track), label, uri, lambda s: _same_track(s.get("item"), track),
                               {"track": {"uri": uri, "name": name}}, allow_launch)
        return self._start(token, ctx, [{"context_uri": uri}], label, uri,
                           lambda s: (s.get("context") or {}).get("uri") == uri
                           or any(a.get("uri") == uri for a in (s.get("item") or {}).get("artists", [])), {"uri": uri},
                           allow_launch)

    def _play_playlist(self, token: str, query: str, ctx: ToolContext,
                       allow_launch: bool = True) -> tuple[str, str, dict[str, Any]]:
        found = _json(self._request(token, "GET", "/me/playlists", ctx, params={"limit": 50}))
        playlists = [p for p in found.get("items", []) if isinstance(p, dict) and p.get("uri")]
        wanted = _tokens(query)
        exact = [p for p in playlists if _tokens(p.get("name", "")) == wanted]
        matches = exact or [p for p in playlists if wanted and wanted <= _tokens(p.get("name", ""))]
        if not matches:
            raise _Blocked("unavailable", f"No playlist named “{query}” was found in your Spotify library.",
                           {"blocker": "not_found"})
        if len(matches) > 1:
            names = "; ".join(str(p.get("name", "?")) for p in matches[:5])
            raise _Blocked("needs_input", f"Several of your playlists match “{query}”: {names}. Which one should I play?",
                           {"candidates": [{"uri": p["uri"], "name": p.get("name")} for p in matches[:5]]})
        uri, name = matches[0]["uri"], matches[0].get("name", "playlist")
        return self._start(token, ctx, [{"context_uri": uri}], f"your playlist {name}", uri,
                           lambda s: (s.get("context") or {}).get("uri") == uri, {"playlist": name}, allow_launch)

    def _start(self, token: str, ctx: ToolContext, bodies: list[dict[str, Any]], label: str, open_uri: str,
               playing: Callable[[dict[str, Any]], bool], extra: dict[str, Any],
               allow_launch: bool = True) -> tuple[str, str, dict[str, Any]]:
        """Start playback on a device and confirm it from Spotify's player state.

        `bodies` are equivalent play requests, tried in order until one is confirmed: Spotify answers 204 to a
        request the app then does not carry out, so an accepted request proves nothing by itself.
        """
        device = self._ensure_device(token, ctx, allow_launch)
        if not device.get("is_active"):
            device = self._activate(token, device, ctx)
        last: dict[str, Any] = {}
        for number, body in enumerate(bodies, 1):
            self._send_play(token, ctx, device, body, label, open_uri, extra, allow_launch)
            state, last = self._verify_detail(token, ctx, lambda s: bool(s.get("is_playing")) and playing(s))
            if state is not None:
                device_name = (state.get("device") or {}).get("name") or device.get("name") or "Spotify"
                return ("succeeded", f"Playing {label} on Spotify ({device_name}).",
                        {"verified": True, "outcome": "SUCCESS", "device": device_name, **extra})
            log.warning("Spotify accepted a play request that did not start", extra={"extra_data": {
                "request": number, "of": len(bodies), "form": "+".join(sorted(body)), "device_id": device.get("id")}})
            if ctx.remaining() < 3:
                break
        device_name = (last.get("device") or {}).get("name") or device.get("name") or "Spotify"
        data = {"verified": False, "device": device_name, "device_id": device.get("id"), **extra}
        other = last.get("item") if last.get("is_playing") else None
        if isinstance(other, dict) and other.get("name"):
            return ("unverified", f"Spotify accepted the request, but NOVA could not confirm that {label} is playing: "
                    f"Spotify reports {_track_label(other)} playing instead.",
                    {**data, "outcome": "PLAYBACK_FAILED", "blocker": "wrong_track"})
        return ("unverified", f"Spotify accepted the request, but NOVA could not confirm that {label} is playing: "
                f"Spotify reports nothing playing on {device_name}. Press play once in the Spotify app, then ask again.",
                {**data, "outcome": "VERIFICATION_TIMEOUT", "blocker": "not_started"})

    def _send_play(self, token: str, ctx: ToolContext, device: dict[str, Any], body: dict[str, Any], label: str,
                   open_uri: str, extra: dict[str, Any], allow_launch: bool) -> None:
        """One accepted PUT /me/player/play, or a precise _Blocked."""
        for attempt in range(2):
            log.info("Spotify playback request", extra={"extra_data": {"method": "PUT", "path": "/me/player/play",
                                                                        "device_id": device["id"], "attempt": attempt + 1,
                                                                        "form": "+".join(sorted(body))}})
            response = self._request(token, "PUT", "/me/player/play", ctx, params={"device_id": device["id"]},
                                     json=body, allow=(403, 404))
            if response.status_code == 404 and attempt == 0:
                ctx.sleep(1.0)  # the device may still be starting up: look it up again once
                device.update(self._ensure_device(token, ctx, allow_launch))
                continue
            if response.status_code == 403:
                reason = _reason(response)
                if reason == "PREMIUM_REQUIRED":
                    opened = allow_launch and self.launcher(open_uri)
                    message = "Spotify Premium is required for NOVA to start playback. " + (
                        f"I opened {label} in the Spotify app instead; press play there." if opened else
                        "Open Spotify and start it there.")
                    raise _Blocked("unverified", message, {"blocker": "premium_required", "outcome": "PLAYBACK_FAILED",
                                                           **extra})
                error_text = _error_text(response).casefold()
                if reason in {"RESTRICTION_VIOLATED", "DEVICE_NOT_CONTROLLABLE"}:
                    raise _Blocked("unavailable", "Spotify reports that the selected device is restricted or cannot be "
                                   "controlled. Choose an available device in Spotify Connect and try again.",
                                   {"blocker": "device_restricted", "device_id": device.get("id")})
                if "scope" in error_text or "permission" in error_text:
                    raise _Blocked("needs_authorization", "Spotify did not grant NOVA the playback permissions it needs. "
                                   "Reconnect Spotify in Settings → Voice → Spotify.",
                                   {"connect_url": CONNECT_URL, "blocker": "insufficient_scope"})
                raise _Blocked("failed", "Spotify refused to start playback. Check the account's plan and the permissions "
                               "granted to NOVA.", {"blocker": "forbidden"})
            if response.status_code == 404:
                raise _Blocked("unavailable", "Spotify lost the selected playback device. Open Spotify on the device you want, "
                               "select it in Spotify Connect, and try again.",
                               {"blocker": "no_device"})
            return

    def _activate(self, token: str, device: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Transfer playback to an idle device and wait until Spotify reports it active.

        An idle device is listed but ignores play requests (they are still answered 204). The transfer does not
        start playback by itself. If the device does not become active in time, the play request is still tried.
        """
        log.info("Spotify playback transfer", extra={"extra_data": {"method": "PUT", "path": "/me/player",
                                                                     "device_id": device.get("id")}})
        response = self._request(token, "PUT", "/me/player", ctx, json={"device_ids": [device["id"]], "play": False},
                                 allow=(403, 404))
        if response.status_code >= 400:
            return device  # the play request reports the precise reason
        until = min(ctx.deadline - 1.0, time.monotonic() + TRANSFER_WAIT_SECONDS)
        while time.monotonic() + self.poll_interval * 2 < until:
            ctx.sleep(self.poll_interval * 2)
            current = next((d for d in self._devices(token, ctx) if d.get("id") == device["id"]), None)
            if current is not None and current.get("is_active"):
                return current
        return device

    def _now_playing(self, token: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        state = self._player(token, ctx)
        item = state.get("item")
        if not isinstance(item, dict) or not item.get("name"):
            return ("succeeded", "Nothing is playing on Spotify right now.",
                    {"verified": True, "is_playing": False})
        device_name = (state.get("device") or {}).get("name") or "Spotify"
        playing = bool(state.get("is_playing"))
        message = (f"Spotify is playing {_track_label(item)} on {device_name}." if playing else
                   f"Spotify is paused on {_track_label(item)} ({device_name}).")
        data: dict[str, Any] = {"verified": True, "is_playing": playing, "device": device_name}
        if item.get("uri"):
            data["track"] = _summary(item)
        return "succeeded", message, data

    # ---- controls -------------------------------------------------------------------------------------------
    def _control(self, token: str, action: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        current = self._player(token, ctx)
        if not current:
            raise _Blocked("unavailable", "Spotify has no active playback right now. Start something in Spotify, or ask me "
                           "to play a track, first.", {"blocker": "no_playback"})
        if action == "play_pause":
            action = "pause" if current.get("is_playing") else "resume"
        before = (current.get("item") or {}).get("uri")
        if action in ("volume_up", "volume_down"):
            volume = (current.get("device") or {}).get("volume_percent")
            if volume is None:
                raise _Blocked("unavailable", "Spotify does not report a volume for the current device.",
                               {"blocker": "no_volume"})
            target = max(0, min(100, int(volume) + (10 if action == "volume_up" else -10)))
            self._request(token, "PUT", "/me/player/volume", ctx, params={"volume_percent": target})
            if self._verify(token, ctx, lambda s: (s.get("device") or {}).get("volume_percent") == target) is None:
                return "unverified", "Spotify accepted the volume change, but NOVA could not confirm it.", {"verified": False}
            return "succeeded", f"Spotify volume set to {target}%.", {"verified": True, "volume": target}
        steps: dict[str, tuple[str, str, Callable[[dict[str, Any]], bool], str]] = {
            "pause": ("PUT", "/me/player/pause", lambda s: not s.get("is_playing"), "Paused Spotify."),
            "resume": ("PUT", "/me/player/play", lambda s: bool(s.get("is_playing")), "Resumed Spotify."),
            "next": ("POST", "/me/player/next", lambda s: (s.get("item") or {}).get("uri") not in (None, before),
                     "Skipped to the next track"),
            "previous": ("POST", "/me/player/previous",
                         lambda s: (s.get("item") or {}).get("uri") != before or int(s.get("progress_ms") or 0) < 5000,
                         "Went back to the previous track"),
        }
        method, path, check, done = steps[action]
        self._request(token, method, path, ctx)
        state = self._verify(token, ctx, check)
        if state is None:
            return "unverified", f"Spotify accepted the request ({action}), but NOVA could not confirm it.", {"verified": False}
        if action in ("next", "previous"):
            done = f"{done}: {_track_label(state['item'])}." if state.get("item") else f"{done}."
        return "succeeded", done, {"verified": True}

    # ---- devices and state ----------------------------------------------------------------------------------
    def _ensure_device(self, token: str, ctx: ToolContext, allow_launch: bool = True) -> dict[str, Any]:
        devices = self._devices(token, ctx)
        device = _pick_device(devices)
        if device is not None:
            return device
        if not allow_launch:
            if devices and all(d.get("is_restricted") for d in devices):
                raise _Blocked("unavailable", "Spotify devices are available, but all are restricted. Select an available "
                               "device in Spotify Connect, then try again.", {"blocker": "device_restricted"})
            raise _Blocked("unavailable", "There is no available Spotify playback device. Open Spotify on the device you "
                           "want and sign in, then ask again. (NOVA does not open the app itself while desktop "
                           "control is off.)", {"blocker": "no_device"})
        # The Spotify desktop app registers itself as a playback device once it is running.
        launched = self.launcher("spotify:")
        until = min(ctx.deadline - 1.0, time.monotonic() + DEVICE_WAIT_SECONDS)
        while launched and time.monotonic() + self.poll_interval * 2 < until:
            ctx.sleep(self.poll_interval * 2)
            devices = self._devices(token, ctx)
            device = _pick_device(devices)
            if device is not None:
                return device
        if not launched:
            raise _Blocked("unavailable", "There is no available Spotify playback device and Windows could not open the app. "
                           "Open Spotify on the device you want, sign in, select it in Spotify Connect, then try again.",
                           {"blocker": "no_device"})
        restricted = bool(devices) and all(d.get("is_restricted") for d in devices)
        if restricted:
            raise _Blocked("unavailable", "Spotify devices are available, but all are restricted. Select an available device "
                           "in Spotify Connect, then try again.", {"blocker": "device_restricted"})
        raise _Blocked("unavailable", "I opened the Spotify app, but no Spotify playback device appeared. Make sure you are "
                       "signed in on the device you want, select it in Spotify Connect, then ask again.",
                       {"blocker": "no_device"})

    def _devices(self, token: str, ctx: ToolContext) -> list[dict[str, Any]]:
        devices = _json(self._request(token, "GET", "/me/player/devices", ctx)).get("devices", [])
        return [d for d in devices if isinstance(d, dict)]

    def _player(self, token: str, ctx: ToolContext) -> dict[str, Any]:
        response = self._request(token, "GET", "/me/player", ctx, allow=(204,))
        state = _json(response)
        log.info("Spotify playback state", extra={"extra_data": {"status": response.status_code,
                                                                    "body": _safe_body(response),
                                                                    "state": redact(state)}})
        return state

    def _verify(self, token: str, ctx: ToolContext, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        """Poll Spotify's reported player state until it matches, for a few seconds at most."""
        return self._verify_detail(token, ctx, predicate)[0]

    def _verify_detail(self, token: str, ctx: ToolContext,
                       predicate: Callable[[dict[str, Any]], bool]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """(the matching state or None, the last state Spotify reported), so a timeout can say what it saw."""
        until = min(ctx.deadline - 0.2, time.monotonic() + VERIFY_SECONDS)
        while True:
            state = self._player(token, ctx)
            if state and predicate(state):
                return state, state
            if time.monotonic() + self.poll_interval >= until:
                return None, state
            ctx.sleep(self.poll_interval)

    # ---- HTTP -----------------------------------------------------------------------------------------------
    def _access_token(self, ctx: ToolContext) -> str:
        tokens = self.tokens.load()
        if not tokens:
            raise _Blocked("needs_authorization", "Spotify isn't connected to NOVA yet. Connect your account (Settings → Voice "
                           "→ Spotify → Connect Spotify), then ask again.",
                           {"connect_url": CONNECT_URL, "blocker": "not_connected"})
        granted = set(str(tokens.get("scope") or "").split())
        if granted and not PLAYBACK_SCOPES <= granted:  # tokens saved without a scope list are checked by Spotify
            raise _Blocked("needs_authorization", "Spotify did not grant NOVA the playback permissions it needs. "
                           "Reconnect Spotify in Settings → Voice → Spotify.",
                           {"connect_url": CONNECT_URL, "blocker": "insufficient_scope"})
        if float(tokens.get("expires_at", 0)) < time.time() + 30:
            ctx.check()
            try:
                response = self._http().post(TOKEN, data={"client_id": self.client_id, "grant_type": "refresh_token",
                                                          "refresh_token": tokens.get("refresh_token")}, timeout=12)
            except httpx.HTTPError:
                raise _Blocked("failed", "Spotify could not be reached to refresh authorization. Check your internet "
                               "connection and try again.", {"blocker": "network"}) from None
            if response.status_code != 200:
                self.tokens.delete()
                raise _Blocked("needs_authorization", "Spotify authorization expired. Reconnect your account in Settings → "
                               "Voice → Spotify.", {"connect_url": CONNECT_URL, "blocker": "expired"})
            body = response.json()
            tokens["access_token"] = body["access_token"]
            tokens["expires_at"] = time.time() + int(body.get("expires_in", 3600))
            tokens["refresh_token"] = body.get("refresh_token", tokens["refresh_token"])
            self.tokens.save(tokens)
        return str(tokens["access_token"])

    def _request(self, token: str, method: str, path: str, ctx: ToolContext, *, allow: tuple[int, ...] = (),
                 **kwargs: Any) -> httpx.Response:
        """One API call with a bounded retry for rate limits and server errors."""
        for attempt in range(2):
            ctx.check()
            try:
                response = self._http().request(method, API + path, headers={"Authorization": f"Bearer {token}"},
                                                timeout=max(1.0, min(10.0, ctx.remaining())), **kwargs)
            except httpx.TimeoutException:
                log.warning("Spotify API request timed out", extra={"extra_data": {"method": method, "path": path,
                                                                                   "attempt": attempt + 1}})
                if attempt == 0 and ctx.remaining() > 3:
                    continue
                raise _Blocked("failed", "Spotify did not answer in time. Try again in a moment.",
                               {"blocker": "timeout"}) from None
            except httpx.HTTPError:
                log.warning("Spotify API request failed", extra={"extra_data": {"method": method, "path": path,
                                                                                  "attempt": attempt + 1,
                                                                                  "error": "network"}})
                raise _Blocked("failed", "Spotify could not be reached. Check your internet connection and try again.",
                               {"blocker": "network"}) from None
            log.info("Spotify API response", extra={"extra_data": {"method": method, "path": path,
                                                                     "status": response.status_code,
                                                                     "body": _safe_body(response)}})
            if response.status_code == 429:
                wait = _retry_after(response)
                if attempt == 0 and wait <= min(3.0, ctx.remaining() - 1):
                    ctx.sleep(wait)
                    continue
                raise _Blocked("failed", "Spotify is rate limiting NOVA right now. Wait a moment and try again.",
                               {"blocker": "rate_limited"})
            if response.status_code >= 500 and attempt == 0:
                ctx.sleep(0.5)
                continue
            if response.status_code in allow:
                return response
            if response.status_code == 401:
                raise _Blocked("needs_authorization", "Spotify rejected NOVA's authorization. Reconnect your account in "
                               "Settings → Voice → Spotify.", {"connect_url": CONNECT_URL, "blocker": "unauthorized"})
            if response.status_code == 403:
                if "scope" in _error_text(response).casefold() or "permission" in _error_text(response).casefold():
                    raise _Blocked("needs_authorization", "Spotify did not grant NOVA the permissions it needs. Reconnect "
                                   "Spotify in Settings → Voice → Spotify.",
                                   {"connect_url": CONNECT_URL, "blocker": "insufficient_scope"})
                raise _Blocked("failed", "Spotify denied this request. Check the account's plan and the permissions granted "
                               "to NOVA.", {"blocker": _reason(response).lower() or "forbidden"})
            if response.status_code >= 400:
                if response.status_code >= 500:
                    raise _Blocked("failed", "Spotify is temporarily unavailable. Try again in a moment.",
                                   {"blocker": "transient_error", "http_status": response.status_code})
                raise _Blocked("failed", f"Spotify could not complete the request (HTTP {response.status_code}).",
                               {"blocker": "api_error"})
            return response
        raise _Blocked("failed", "Spotify kept failing; try again later.", {"blocker": "api_error"})


class _Blocked(Exception):
    """A precise, user-facing reason that ends the current operation."""

    def __init__(self, status: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data or {}


# ---- matching helpers -------------------------------------------------------------------------------------
def _tokens(text: str) -> frozenset[str]:
    plain = unicodedata.normalize("NFKD", text.casefold().replace("œ", "oe").replace("æ", "ae"))
    plain = "".join(c for c in plain if not unicodedata.combining(c)).replace("'", "")
    return frozenset(re.findall(r"[a-z0-9]+", plain))


def _clean(text: str) -> str:
    """A search phrase without separators that confuse Spotify's search ('Artist — Title')."""
    return " ".join(re.sub(r"[—–\-:|/]+", " ", text).split())


def _core_title(name: str) -> str:
    name = re.sub(r"\s*[(\[].*?[)\]]", "", name)  # (Remix), [Live], (feat. X)
    return name.split(" - ")[0]


def _best_track(items: list[dict[str, Any]], wanted: frozenset[str]) -> dict[str, Any] | None:
    """The first result (Spotify's ranking) among those whose title is in the request, preferring the named artist
    and the original version. None when no title matches: then the owner chooses."""

    def artist_named(t: dict[str, Any]) -> bool:
        return any(bool(_tokens(a.get("name", ""))) and _tokens(a.get("name", "")) <= wanted for a in t.get("artists", []))

    titled = [t for t in items if _tokens(_core_title(t.get("name", ""))) and _tokens(_core_title(t.get("name", ""))) <= wanted]
    if not titled:
        return None
    ranked = sorted(enumerate(titled), key=lambda p: (not artist_named(p[1]), not _tokens(p[1].get("name", "")) <= wanted, p[0]))
    return ranked[0][1]


def _artist_request(items: list[dict[str, Any]], wanted: frozenset[str]) -> tuple[str, str] | None:
    """When the request is just an artist's name ("play Tiakola"), that artist."""
    for t in items:
        for a in t.get("artists", []):
            if a.get("uri") and _tokens(a.get("name", "")) == wanted:
                return str(a["uri"]), str(a.get("name"))
    return None


def _same_track(item: dict[str, Any] | None, track: dict[str, Any]) -> bool:
    if not item:
        return False
    linked = (item.get("linked_from") or {}).get("uri")
    return item.get("uri") == track["uri"] or linked == track["uri"] or (bool(track.get("id")) and item.get("id") == track["id"])


def _track_label(track: dict[str, Any]) -> str:
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    return f"{track.get('name', 'Track')} by {artists}" if artists else str(track.get("name", "Track"))


def _track_bodies(track: dict[str, Any]) -> list[dict[str, Any]]:
    """Play requests for one track, most reliable first.

    Observed live (Spotify for Windows 1.308, Premium): a bare {"uris": [track]} request is answered 204 and then
    leaves the player empty, while the same track started inside its album plays. The plain form stays as a fallback
    for tracks without an album and for clients where it works.
    """
    plain = {"uris": [track["uri"]]}
    album = (track.get("album") or {}).get("uri")
    if isinstance(album, str) and album.startswith("spotify:album:"):
        return [{"context_uri": album, "offset": {"uri": track["uri"]}}, plain]
    return [plain]


def _summary(track: dict[str, Any]) -> dict[str, Any]:
    return {"uri": track["uri"], "name": track.get("name"), "artists": [a.get("name") for a in track.get("artists", [])]}


def _pick_device(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [d for d in devices if d.get("id") and not d.get("is_restricted")]
    return (next((d for d in usable if d.get("is_active")), None)
            or next((d for d in usable if d.get("type") == "Computer"), None)
            or (usable[0] if usable else None))


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _reason(response: httpx.Response) -> str:
    error = _json(response).get("error")
    return str(error.get("reason", "")) if isinstance(error, dict) else ""


def _error_text(response: httpx.Response) -> str:
    error = _json(response).get("error")
    if isinstance(error, dict):
        return " ".join(str(error.get(key, "")) for key in ("message", "reason", "error"))
    return ""


def _safe_body(response: httpx.Response) -> Any:
    """Return a bounded, redacted body for diagnostics; never include request headers or tokens."""
    if not response.content:
        return None
    try:
        body = response.json()
    except ValueError:
        body = redact_text(response.text[:4096])
        body = re.sub(r'(?i)("?(?:access|refresh)?_?token|client_secret|authorization)"?\s*[:=]\s*"?[^",\s}]+',
                      r'\1=[REDACTED]', body)
        return body
    return redact(body)


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "1")))
    except ValueError:
        return 1.0

