"""Spotify Web API integration against MOCKED HTTP responses.

These prove NOVA's search, device, playback, verification and error handling logic. They do not prove that a live
Spotify account works; see docs/ACTIONS.md for the live test procedure.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.tools.registry import ToolCancelled, ToolContext
from app.tools.spotify import SpotifyService

TIAKOLA = {"uri": "spotify:track:pirate", "id": "pirate", "name": "Vie de Pirate", "artists": [{"name": "Tiakola"}]}
REMIX = {"uri": "spotify:track:remix", "id": "remix", "name": "Vie de Pirate (Remix)", "artists": [{"name": "DJ Other"}]}
OTHER = {"uri": "spotify:track:other", "id": "other", "name": "Pirates", "artists": [{"name": "Someone"}]}
OLD = {"uri": "spotify:track:old", "id": "old", "name": "Old Song", "artists": [{"name": "Earlier"}]}
PC = {"id": "pc-1", "name": "FAREL-PC", "type": "Computer", "is_active": True, "is_restricted": False}


class MemoryTokens:
    def __init__(self):
        self.value = None

    def save(self, value):
        self.value = dict(value)

    def load(self):
        return dict(self.value) if self.value else None

    def delete(self):
        self.value = None


class FakeSpotify:
    """A small scripted stand-in for the Spotify Web API."""

    def __init__(self, *, devices=None, search=None, player_states=None, play_status=204, play_body=None,
                 devices_after_launch=None):
        self.devices = [PC] if devices is None else devices
        self.devices_after_launch = devices_after_launch
        self.launched = []
        self.search_items = [TIAKOLA] if search is None else search
        self.player_states = list(player_states or [])
        self.play_status = play_status
        self.play_body = play_body
        self.calls: list[tuple[str, str]] = []
        self.played: list[dict] = []

    def launcher(self, uri: str) -> bool:
        self.launched.append(uri)
        if self.devices_after_launch is not None:
            self.devices = self.devices_after_launch
        return True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/me/player/devices"):
            return httpx.Response(200, json={"devices": self.devices})
        if path.endswith("/search"):
            self.search_params = dict(request.url.params)
            return httpx.Response(200, json={"tracks": {"items": self.search_items}})
        if path.endswith("/me/player/play") and request.method == "PUT":
            self.played.append({"params": dict(request.url.params), "body": json.loads(request.content or b"{}")})
            if self.play_status >= 400:
                return httpx.Response(self.play_status, json=self.play_body or {})
            return httpx.Response(self.play_status)
        if path.endswith("/me/player") and request.method == "GET":
            if not self.player_states:
                return httpx.Response(204)
            state = self.player_states.pop(0) if len(self.player_states) > 1 else self.player_states[0]
            return httpx.Response(204) if state is None else httpx.Response(200, json=state)
        if path.endswith("/me/player/pause") or path.endswith("/me/player/next"):
            return httpx.Response(204)
        return httpx.Response(404)


def connected_service(fake: FakeSpotify, tmp_path) -> SpotifyService:
    service = SpotifyService("client-id", 8000, tmp_path / "token.bin", transport=httpx.MockTransport(fake),
                             launcher=fake.launcher, poll_interval=0.01)
    service.tokens = MemoryTokens()
    service.tokens.save({"access_token": "access", "refresh_token": "refresh", "expires_at": time.time() + 3600})
    return service


def ctx(seconds: float = 10) -> ToolContext:
    return ToolContext(deadline=time.monotonic() + seconds)


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    monkeypatch.setattr("app.tools.spotify.sys.platform", "win32")


def test_pkce_authorization_is_state_bound_and_uses_verifier(tmp_path):
    seen = {}

    def fake(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                                         "scope": "user-modify-playback-state"})

    service = SpotifyService("client-id", 8000, tmp_path / "token.bin", transport=httpx.MockTransport(fake))
    service.tokens = MemoryTokens()
    auth_url = service.begin(7)
    params = parse_qs(urlparse(auth_url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert "user-read-private" in params["scope"][0].split()
    assert service.finish("one-time-code", params["state"][0]) == 7
    assert seen["code_verifier"] and service.tokens.load()["refresh_token"] == "refresh"
    with pytest.raises(Exception, match="state did not match"):
        service.finish("replay", params["state"][0])
    service.close()


def test_exact_request_plays_the_intended_track_and_verifies_reported_state(tmp_path):
    # Spotify reports the previous track first; NOVA keeps checking until the requested one is really playing.
    fake = FakeSpotify(search=[REMIX, OTHER, TIAKOLA],
                       player_states=[{"is_playing": True, "item": OLD, "device": PC},
                                      {"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola — Vie de Pirate", ctx=ctx())
    assert status == "succeeded", message
    assert message == "Playing Vie de Pirate by Tiakola on Spotify (FAREL-PC)."
    assert data["verified"] is True and data["track"]["uri"] == "spotify:track:pirate"
    assert fake.played == [{"params": {"device_id": "pc-1"}, "body": {"uris": ["spotify:track:pirate"]}}]
    service.close()


def test_empty_204_play_response_is_accepted_and_id_only_state_confirms_playback(tmp_path):
    state_item = {"id": "pirate", "name": "No No No", "artists": [{"name": "Beri Boys Club"}]}
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": state_item, "device": PC}], play_status=204)
    service = connected_service(fake, tmp_path)
    status, _, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "succeeded" and data["verified"] is True
    assert fake.played and fake.played[0]["params"]["device_id"] == PC["id"]
    service.close()


def test_artist_given_separately_is_used_for_the_search_and_the_match(tmp_path):
    fake = FakeSpotify(search=[REMIX, TIAKOLA], player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, _, data = service.command("play_track", "Vie de Pirate", artist="Tiakola", ctx=ctx())
    assert status == "succeeded" and data["track"]["uri"] == TIAKOLA["uri"]
    service.close()


def test_title_and_artist_swapped_by_the_model_still_find_the_track(tmp_path):
    # Live finding: llama3.2 sent query="Tiakola", artist="Vie de Pirate"; an artist: filter then found nothing.
    fake = FakeSpotify(search=[REMIX, TIAKOLA], player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, _, data = service.command("play_track", "Tiakola", artist="Vie de Pirate", ctx=ctx())
    assert status == "succeeded" and data["track"]["uri"] == TIAKOLA["uri"]
    assert fake.search_params["q"] == "Tiakola Vie de Pirate"
    service.close()


def test_title_only_request_follows_spotify_ranking_among_exact_titles(tmp_path):
    starboy = {"uri": "spotify:track:1", "id": "1", "name": "Starboy", "artists": [{"name": "The Weeknd"}]}
    cover = {"uri": "spotify:track:2", "id": "2", "name": "Starboy", "artists": [{"name": "Cover Band"}]}
    fake = FakeSpotify(search=[starboy, cover], player_states=[{"is_playing": True, "item": starboy, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, _ = service.command("play_track", "Starboy", ctx=ctx())
    assert status == "succeeded" and "The Weeknd" in message
    service.close()


def test_ambiguous_results_ask_which_track_instead_of_guessing(tmp_path):
    fake = FakeSpotify(search=[REMIX, OTHER, OLD])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "that pirate song", ctx=ctx())
    assert status == "needs_input"
    assert [c["uri"] for c in data["candidates"]] == [REMIX["uri"], OTHER["uri"], OLD["uri"]]
    assert "Which one" in message
    assert fake.played == []
    service.close()


def test_search_uses_the_authorized_users_market_for_playability_and_relinking(tmp_path):
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert fake.search_params["market"] == "from_token" and fake.search_params["type"] == "track"
    service.close()


def test_unplayable_search_results_are_skipped_for_a_playable_relinked_track(tmp_path):
    unavailable = {**TIAKOLA, "uri": "spotify:track:unavailable", "id": "unavailable",
                   "is_playable": False, "restrictions": {"reason": "market"}}
    relinked = {**TIAKOLA, "uri": "spotify:track:relinked", "id": "relinked",
                "linked_from": {"uri": TIAKOLA["uri"]}, "is_playable": True}
    fake = FakeSpotify(search=[unavailable, relinked],
                       player_states=[{"is_playing": True, "item": relinked, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, _, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "succeeded" and data["track"]["uri"] == relinked["uri"]
    assert fake.played[0]["body"] == {"uris": [relinked["uri"]]}
    service.close()


def test_market_unavailable_track_is_reported_before_play_request(tmp_path):
    unavailable = {**TIAKOLA, "is_playable": False, "restrictions": {"reason": "market"}}
    fake = FakeSpotify(search=[unavailable])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "unavailable" and "not playable in your account's market" in message
    assert data["blocker"] == "market_unavailable" and fake.played == []
    service.close()


def test_misremembered_title_offers_the_real_tracks_instead_of_guessing(tmp_path):
    # Live finding: Spotify has no Tiakola track named "Vie de Pirate"; these were its real top results.
    coeur = {"uri": "spotify:track:coeur", "id": "coeur", "name": "Cœur De Pirate", "artists": [{"name": "Tiakola"}]}
    mode = {"uri": "spotify:track:mode", "id": "mode", "name": "Mode de vie", "artists": [{"name": "Tiakola"}]}
    damso = {"uri": "spotify:track:damso", "id": "damso", "name": "Coeur De Pirate", "artists": [{"name": "Damso"}]}
    fake = FakeSpotify(search=[coeur, mode, damso])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola — Vie de Pirate", ctx=ctx())
    assert status == "needs_input" and fake.played == []
    assert data["candidates"][0] == {"uri": "spotify:track:coeur", "name": "Cœur De Pirate", "artists": ["Tiakola"]}
    assert "1. Cœur De Pirate by Tiakola" in message
    # The owner's answer, as the model passes it on, plays the real track.
    fake.player_states = [{"is_playing": True, "item": coeur, "device": PC}]
    status, message, _ = service.command("play_track", "Cœur De Pirate", artist="Tiakola", ctx=ctx())
    assert status == "succeeded" and message == "Playing Cœur De Pirate by Tiakola on Spotify (FAREL-PC)."
    service.close()


def test_missing_authorization_asks_to_connect_with_the_supported_flow(tmp_path):
    fake = FakeSpotify()
    service = SpotifyService("client-id", 8000, tmp_path / "token.bin", transport=httpx.MockTransport(fake),
                             launcher=fake.launcher)
    service.tokens = MemoryTokens()
    status, message, data = service.command("play_track", "Vie de Pirate", ctx=ctx())
    assert status == "needs_authorization" and "Connect" in message
    assert data["connect_url"] == "/api/spotify/connect"
    assert fake.calls == []
    service.close()


def test_missing_client_id_is_reported_as_configuration_problem(tmp_path):
    service = SpotifyService("", 8000, tmp_path / "missing.bin")
    status, message, _ = service.command("play_track", "Song", ctx=ctx())
    assert status == "unavailable" and "NOVA_SPOTIFY_CLIENT_ID" in message
    service.close()


def test_rejected_authorization_asks_to_reconnect(tmp_path):
    service = SpotifyService("client-id", 8000, tmp_path / "t.bin",
                             transport=httpx.MockTransport(lambda _: httpx.Response(401)), launcher=lambda _: True)
    service.tokens = MemoryTokens()
    service.tokens.save({"access_token": "expired", "refresh_token": "refresh", "expires_at": time.time() + 3600})
    status, message, data = service.command("play_track", "Song", ctx=ctx())
    assert status == "needs_authorization" and "reconnect" in message.lower()
    assert data["connect_url"] == "/api/spotify/connect"
    service.close()


def test_no_device_launches_the_spotify_app_and_waits_for_it(tmp_path):
    fake = FakeSpotify(devices=[], devices_after_launch=[{**PC, "is_active": False}],
                       player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, _ = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert fake.launched == ["spotify:"]
    assert status == "succeeded", message
    service.close()


def test_no_device_even_after_launch_names_the_precise_blocker(tmp_path):
    fake = FakeSpotify(devices=[])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx(3))
    assert status == "unavailable" and "no Spotify playback device" in message
    assert data["blocker"] == "no_device" and fake.launched == ["spotify:"]
    assert fake.played == []
    service.close()


def test_premium_required_is_explained_and_the_track_is_opened_in_the_app(tmp_path):
    fake = FakeSpotify(play_status=403, play_body={"error": {"status": 403, "message": "Player command failed: Premium required",
                                                             "reason": "PREMIUM_REQUIRED"}})
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "unverified" and "Premium" in message
    assert data["blocker"] == "premium_required" and data["verified"] is False
    assert "spotify:track:pirate" in fake.launched
    service.close()


def test_insufficient_scope_requests_reauthorization(tmp_path):
    fake = FakeSpotify(play_status=403, play_body={"error": {"status": 403, "message": "Insufficient scope"}})
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "needs_authorization" and "permissions" in message
    assert data["blocker"] == "insufficient_scope" and data["connect_url"] == "/api/spotify/connect"
    service.close()


def test_restricted_device_is_reported_actionably(tmp_path):
    restricted = {**PC, "is_restricted": True}
    fake = FakeSpotify(devices=[restricted])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx(2))
    assert status == "unavailable" and "Spotify Connect" in message
    assert data["blocker"] == "device_restricted" and fake.played == []
    service.close()


def test_transient_spotify_server_error_is_distinct(tmp_path):
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}})
        return FakeSpotify()(request)

    service = SpotifyService("client-id", 8000, tmp_path / "t.bin", transport=httpx.MockTransport(failing),
                             launcher=lambda _: True, poll_interval=0.01)
    service.tokens = MemoryTokens()
    service.tokens.save({"access_token": "do-not-log-access", "refresh_token": "do-not-log-refresh",
                         "expires_at": time.time() + 3600})
    status, message, data = service.command("play_track", "Song", ctx=ctx())
    assert status == "failed" and "temporarily unavailable" in message
    assert data["blocker"] == "transient_error"
    service.close()


def test_spotify_diagnostics_include_status_body_and_device_without_secrets(tmp_path, caplog):
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    with caplog.at_level("INFO", logger="nova.spotify"):
        status, _, _ = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "succeeded"
    messages = [record.getMessage() for record in caplog.records]
    assert "Spotify API response" in messages and "Spotify playback state" in messages
    structured = [getattr(record, "extra_data", {}) for record in caplog.records]
    assert any(item.get("device_id") == PC["id"] for item in structured)
    assert "do-not-log-access" not in caplog.text and "do-not-log-refresh" not in caplog.text
    service.close()


def test_unconfirmed_playback_is_never_reported_as_success(tmp_path):
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": OLD, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx(2))
    assert status == "unverified" and "could not confirm" in message
    assert data["verified"] is False
    service.close()


def test_rate_limit_is_retried_once_then_reported(tmp_path):
    attempts = {"search": 0}

    def limited(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            attempts["search"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        return FakeSpotify()(request)

    service = SpotifyService("client-id", 8000, tmp_path / "t.bin", transport=httpx.MockTransport(limited),
                             launcher=lambda _: True, poll_interval=0.01)
    service.tokens = MemoryTokens()
    service.tokens.save({"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600})
    status, message, _ = service.command("play_track", "Tiakola Vie de Pirate", ctx=ctx())
    assert status == "failed" and "rate limit" in message.lower()
    assert attempts["search"] == 2
    service.close()


def test_controls_handle_no_active_playback_without_crashing(tmp_path):
    fake = FakeSpotify(player_states=[None])
    service = connected_service(fake, tmp_path)
    for action in ("pause", "play_pause", "volume_up"):
        status, message, _ = service.command(action, ctx=ctx())
        assert status == "unavailable" and "no active playback" in message.lower()
    service.close()


def test_pause_is_verified_from_reported_state(tmp_path):
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC},
                                      {"is_playing": False, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("pause", ctx=ctx())
    assert status == "succeeded" and message == "Paused Spotify." and data["verified"] is True
    service.close()


def test_cancellation_stops_waiting_for_a_device(tmp_path):
    fake = FakeSpotify(devices=[])
    service = connected_service(fake, tmp_path)
    c = ctx(30)
    c.cancel()
    with pytest.raises(ToolCancelled):
        service.command("play_track", "Tiakola Vie de Pirate", ctx=c)
    service.close()


def test_a_chosen_track_is_played_by_its_exact_spotify_id(tmp_path):
    fake = FakeSpotify(player_states=[{"is_playing": True, "item": TIAKOLA, "device": PC}])
    service = connected_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Vie de Pirate", artist="Tiakola", uri="spotify:track:pirate",
                                            ctx=ctx())
    assert status == "succeeded" and data["verified"] is True
    assert message == "Playing Vie de Pirate by Tiakola on Spotify (FAREL-PC)."
    assert ("GET", "/v1/search") not in fake.calls
    assert fake.played[0]["body"] == {"uris": ["spotify:track:pirate"]}
    service.close()


# ---- playback that really starts (regression: "Spotify accepted the request, but NOVA could not confirm…") ----------
ALBUM = {"uri": "spotify:album:deathrace", "name": "Death Race for Love"}
BANDIT = {"uri": "spotify:track:bandit", "id": "bandit", "name": "Bandit (with YoungBoy Never Broke Again)",
          "artists": [{"name": "Juice WRLD"}, {"name": "YoungBoy Never Broke Again"}], "album": ALBUM}
BANDIT_COVER = {"uri": "spotify:track:cover", "id": "cover", "name": "Bandit", "artists": [{"name": "Cover Band"}],
                "album": {"uri": "spotify:album:covers"}}
IDLE_PC = {"id": "pc-1", "name": "LAPTOP", "type": "Computer", "is_active": False, "is_restricted": False}
ALL_SCOPES = "user-read-playback-state user-modify-playback-state user-read-private"


class DesktopSpotify:
    """The Spotify Web API as it behaved in the live diagnosis (Spotify for Windows 1.308, Premium).

    - Every play request is answered 204, whether or not the app carries it out.
    - An idle device ignores play requests until playback is transferred to it.
    - A bare {"uris": [...]} request empties the player; the same track started inside its album plays.
    """

    def __init__(self, *, devices=None, search=None, plain_works=False, context_works=True, plays_instead=None,
                 play_error=None, tracks=None):
        self.devices = [dict(d) for d in ([IDLE_PC] if devices is None else devices)]
        self.search_items = [BANDIT] if search is None else search
        self.catalog = {t["id"]: t for t in (tracks if tracks is not None else self.search_items)}
        self.plain_works, self.context_works, self.plays_instead = plain_works, context_works, plays_instead
        self.play_error = play_error
        self.state: dict | None = None
        self.calls: list[tuple[str, str]] = []
        self.played: list[dict] = []
        self.transfers: list[dict] = []
        self.launched: list[str] = []

    def launcher(self, uri: str) -> bool:
        self.launched.append(uri)
        return True

    def _active(self):
        return next((d for d in self.devices if d["is_active"]), None)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if path.endswith("/me/player/devices"):
            return httpx.Response(200, json={"devices": self.devices})
        if path.endswith("/search"):
            return httpx.Response(200, json={"tracks": {"items": self.search_items}})
        if "/tracks/" in path:
            track = self.catalog.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=track) if track else httpx.Response(404)
        if path.endswith("/me/player") and method == "PUT":
            body = json.loads(request.content)
            self.transfers.append(body)
            for d in self.devices:
                d["is_active"] = d["id"] in body["device_ids"]
            self.state = {"is_playing": False, "item": None, "device": self._active()}
            return httpx.Response(204)
        if path.endswith("/me/player/play"):
            body = json.loads(request.content or b"{}")
            self.played.append({"params": dict(request.url.params), "body": body})
            if self.play_error:
                return httpx.Response(self.play_error[0], json=self.play_error[1])
            device = self._active()
            if device is None or device["id"] != request.url.params.get("device_id"):
                return httpx.Response(204)  # accepted, ignored
            works = self.context_works if "context_uri" in body else self.plain_works
            uri = (body.get("offset") or {}).get("uri") or (body.get("uris") or [None])[0]
            item = next((t for t in self.catalog.values() if t["uri"] == uri), None)
            if self.plays_instead:
                self.state = {"is_playing": True, "item": self.plays_instead, "device": device}
            elif works and item:
                self.state = {"is_playing": True, "item": item, "device": device,
                              "context": {"uri": body.get("context_uri")}}
            else:
                self.state = {"is_playing": False, "item": None, "device": device}
            return httpx.Response(204)
        if path.endswith("/me/player") and method == "GET":
            return httpx.Response(200, json=self.state) if self.state and self._active() else httpx.Response(204)
        return httpx.Response(404)


def desktop_service(fake: DesktopSpotify, tmp_path, *, scope=ALL_SCOPES, expires_in=3600) -> SpotifyService:
    service = SpotifyService("client-id", 8000, tmp_path / "token.bin", transport=httpx.MockTransport(fake),
                             launcher=fake.launcher, poll_interval=0.01)
    service.tokens = MemoryTokens()
    service.tokens.save({"access_token": "access", "refresh_token": "refresh", "expires_at": time.time() + expires_in,
                         "scope": scope})
    return service


def test_idle_desktop_app_is_activated_and_the_track_really_starts(tmp_path, monkeypatch):
    """The reported bug: device listed but idle, bare-URI play accepted (204) and never started."""
    monkeypatch.setattr("app.tools.spotify.VERIFY_SECONDS", 0.2)
    fake = DesktopSpotify()
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Juice WRLD - Bandit", ctx=ctx())
    assert status == "succeeded", message
    assert message == ("Playing Bandit (with YoungBoy Never Broke Again) by Juice WRLD, YoungBoy Never Broke Again "
                       "on Spotify (LAPTOP).")
    assert data["verified"] is True and data["outcome"] == "SUCCESS" and data["track"]["uri"] == "spotify:track:bandit"
    assert fake.transfers == [{"device_ids": ["pc-1"], "play": False}]  # transferred first, without starting old music
    assert fake.calls.index(("PUT", "/v1/me/player")) < fake.calls.index(("PUT", "/v1/me/player/play"))
    assert fake.played == [{"params": {"device_id": "pc-1"},
                            "body": {"context_uri": "spotify:album:deathrace", "offset": {"uri": "spotify:track:bandit"}}}]
    assert fake.launched == []  # the app was already running: nothing is launched
    service.close()


def test_active_device_is_not_transferred(tmp_path):
    fake = DesktopSpotify(devices=[{**IDLE_PC, "is_active": True}])
    service = desktop_service(fake, tmp_path)
    assert service.command("play_track", "Bandit", artist="Juice WRLD", ctx=ctx())[0] == "succeeded"
    assert fake.transfers == []
    service.close()


def test_plain_request_is_the_fallback_when_the_album_request_does_not_start(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.spotify.VERIFY_SECONDS", 0.1)
    fake = DesktopSpotify(context_works=False, plain_works=True)
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "succeeded" and data["outcome"] == "SUCCESS", message
    assert [sorted(p["body"]) for p in fake.played] == [["context_uri", "offset"], ["uris"]]
    service.close()


def test_accepted_request_that_never_starts_is_a_verification_timeout_not_a_success(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.spotify.VERIFY_SECONDS", 0.1)
    fake = DesktopSpotify(context_works=False, plain_works=False)
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "unverified" and "could not confirm" in message and "nothing playing on LAPTOP" in message
    assert data["verified"] is False and data["outcome"] == "VERIFICATION_TIMEOUT"
    assert len(fake.played) == 2  # both request forms were accepted with 204; neither is treated as proof
    service.close()


def test_another_track_playing_is_reported_as_failed_playback(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.spotify.VERIFY_SECONDS", 0.1)
    fake = DesktopSpotify(plays_instead=OLD)
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "unverified" and "Old Song by Earlier playing instead" in message
    assert data["verified"] is False and data["outcome"] == "PLAYBACK_FAILED" and data["blocker"] == "wrong_track"
    service.close()


def test_same_title_by_another_artist_is_not_chosen_over_the_named_artist(tmp_path):
    fake = DesktopSpotify(search=[BANDIT_COVER, BANDIT], devices=[{**IDLE_PC, "is_active": True}])
    service = desktop_service(fake, tmp_path)
    status, _, data = service.command("play_track", "Bandit by Juice WRLD", ctx=ctx())
    assert status == "succeeded" and data["track"]["uri"] == "spotify:track:bandit"
    service.close()


def test_no_matching_track_is_reported_before_any_device_or_play_request(tmp_path):
    fake = DesktopSpotify(search=[])
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "zzzz no such song", ctx=ctx())
    assert status == "unavailable" and "found no track" in message and data["blocker"] == "not_found"
    assert fake.played == [] and ("GET", "/v1/me/player/devices") not in fake.calls
    service.close()


def test_device_discovery_prefers_this_computer_and_skips_restricted_devices(tmp_path):
    speaker = {"id": "spk", "name": "Kitchen", "type": "Speaker", "is_active": False, "is_restricted": True}
    phone = {"id": "ph", "name": "Phone", "type": "Smartphone", "is_active": False, "is_restricted": False}
    fake = DesktopSpotify(devices=[speaker, phone, IDLE_PC])
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "succeeded" and data["device"] == "LAPTOP", message
    assert fake.transfers[0]["device_ids"] == ["pc-1"] and fake.played[0]["params"] == {"device_id": "pc-1"}
    service.close()


def test_no_device_and_restricted_device_have_distinct_outcomes(tmp_path):
    none = desktop_service(DesktopSpotify(devices=[]), tmp_path)
    assert none.command("play_track", "Bandit Juice WRLD", ctx=ctx(2))[2]["outcome"] == "NO_DEVICE"
    none.close()
    restricted = desktop_service(DesktopSpotify(devices=[{**IDLE_PC, "is_restricted": True}]), tmp_path)
    data = restricted.command("play_track", "Bandit Juice WRLD", ctx=ctx(2))[2]
    assert data["outcome"] == "DEVICE_UNAVAILABLE" and data["blocker"] == "device_restricted"
    restricted.close()


def test_refused_play_request_is_playback_failed(tmp_path):
    fake = DesktopSpotify(devices=[{**IDLE_PC, "is_active": True}],
                          play_error=(403, {"error": {"status": 403, "message": "Player command failed", "reason": "UNKNOWN"}}))
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "failed" and "refused" in message
    assert data["outcome"] == "PLAYBACK_FAILED" and data["verified"] is False
    service.close()


def test_missing_playback_scopes_ask_to_reconnect_without_calling_spotify(tmp_path):
    fake = DesktopSpotify()
    service = desktop_service(fake, tmp_path, scope="user-read-private playlist-read-private")
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "needs_authorization" and "permissions" in message
    assert data["outcome"] == "INSUFFICIENT_SCOPE" and data["connect_url"] == "/api/spotify/connect"
    assert fake.calls == []
    service.close()


def test_expired_token_is_refreshed_and_a_dead_refresh_token_asks_to_reconnect(tmp_path):
    refreshes = []
    fake = DesktopSpotify(devices=[{**IDLE_PC, "is_active": True}])

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            refreshes.append(parse_qs(request.content.decode()))
            if refreshes[-1]["refresh_token"] == ["revoked"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer fresh"
        return fake(request)

    service = desktop_service(fake, tmp_path, expires_in=-10)
    service.client = httpx.Client(transport=httpx.MockTransport(api))
    assert service.command("play_track", "Bandit Juice WRLD", ctx=ctx())[0] == "succeeded"
    saved = service.tokens.load()
    assert refreshes[0]["grant_type"] == ["refresh_token"] and saved["access_token"] == "fresh"
    assert saved["refresh_token"] == "refresh" and saved["expires_at"] > time.time() + 3000

    service.tokens.save({**saved, "refresh_token": "revoked", "expires_at": time.time() - 10})
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx())
    assert status == "needs_authorization" and "Reconnect" in message
    assert data["outcome"] == "AUTHENTICATION_REQUIRED" and service.tokens.load() is None
    service.close()


def test_a_chosen_track_is_started_inside_its_album(tmp_path):
    fake = DesktopSpotify(devices=[{**IDLE_PC, "is_active": True}])
    service = desktop_service(fake, tmp_path)
    status, message, _ = service.command("play_track", "Bandit", artist="Juice WRLD", uri="spotify:track:bandit", ctx=ctx())
    assert status == "succeeded", message
    assert ("GET", "/v1/search") not in fake.calls
    assert fake.played[0]["body"] == {"context_uri": "spotify:album:deathrace", "offset": {"uri": "spotify:track:bandit"}}
    service.close()


def test_now_playing_reports_spotifys_own_state(tmp_path):
    fake = DesktopSpotify(devices=[{**IDLE_PC, "is_active": True}])
    service = desktop_service(fake, tmp_path)
    status, message, data = service.command("now_playing", ctx=ctx())
    assert status == "succeeded" and message == "Nothing is playing on Spotify right now." and data["is_playing"] is False
    fake.state = {"is_playing": True, "item": BANDIT, "device": fake.devices[0]}
    status, message, data = service.command("now_playing", ctx=ctx())
    assert message == ("Spotify is playing Bandit (with YoungBoy Never Broke Again) by Juice WRLD, YoungBoy Never Broke "
                       "Again on LAPTOP.")
    assert data["is_playing"] is True and data["track"]["uri"] == "spotify:track:bandit"
    assert not any(method != "GET" for method, _ in fake.calls)
    service.close()


def test_without_app_launching_the_web_api_still_plays_and_nothing_is_started(tmp_path):
    fake = DesktopSpotify()
    service = desktop_service(fake, tmp_path)
    assert service.command("play_track", "Bandit Juice WRLD", ctx=ctx(), allow_launch=False)[0] == "succeeded"
    status, message, data = service.command("open", ctx=ctx(), allow_launch=False)
    assert status == "unavailable" and "NOVA_DESKTOP_CONTROL_ENABLED" in message
    fake.devices.clear()
    status, message, data = service.command("play_track", "Bandit Juice WRLD", ctx=ctx(), allow_launch=False)
    assert status == "unavailable" and data["outcome"] == "NO_DEVICE" and "Open Spotify" in message
    assert fake.launched == []
    service.close()
