"""Windows desktop tools with the OS primitives (launch, process list, media keys) replaced by recorders."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from app.tools import desktop_os
from app.tools.registry import PermissionLevel, ToolContext, ToolRegistry
from app.tools.spotify import SpotifyService
from app.tools.windows_agent import DEFAULT_APPS, App, desktop_tools, parse_apps


class FakeOS:
    def __init__(self, processes=(), launch_ok=True):
        self.launched: list[str] = []
        self.keys: list[str] = []
        self.procs = set(processes)
        self.launch_ok = launch_ok

    def launch(self, target: str) -> bool:
        self.launched.append(target)
        return self.launch_ok

    def processes(self) -> set[str]:
        return self.procs

    def media_key(self, name: str) -> bool:
        self.keys.append(name)
        return True


class NoTokens:
    def load(self):
        return None

    def save(self, _):
        pass

    def delete(self):
        pass


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    monkeypatch.setattr("app.tools.windows_agent.sys.platform", "win32")
    monkeypatch.setattr("app.tools.spotify.sys.platform", "win32")


def registry(fake: FakeOS, *, enabled=True, spotify: SpotifyService | None = None, hosts="github.com,localhost",
             apps=None, app_resolver=None, app_launcher=None) -> ToolRegistry:
    if spotify is None:
        spotify = SpotifyService("client-id", 8000, Path("unused.bin"), launcher=fake.launch, processes=fake.processes,
                                 transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        spotify.tokens = NoTokens()
    return ToolRegistry(desktop_tools(enabled, spotify, apps=apps or DEFAULT_APPS, browser_hosts=hosts,
                                      allowlisted_commands=[["python", "-m", "pytest"]], launcher=fake.launch,
                                      app_launcher=app_launcher,
                                      app_resolver=app_resolver or desktop_os.resolve_application,
                                      processes=fake.processes, media_key=fake.media_key))


def ctx(**kw) -> ToolContext:
    return ToolContext(deadline=time.monotonic() + 10, **kw)


def test_play_request_without_spotify_connection_asks_to_connect_instead_of_crashing():
    # Regression: this path used to raise UnboundLocalError ('os') and report a generic tool error.
    result = registry(FakeOS()).execute("spotify_control", {"action": "play_track", "query": "Tiakola Vie de Pirate"}, ctx())
    assert result.status == "needs_authorization"
    assert result.data["connect_url"] == "/api/spotify/connect"


def test_playback_controls_fall_back_to_media_keys_honestly_when_spotify_is_not_connected():
    fake = FakeOS(processes={"spotify.exe"})
    result = registry(fake).execute("spotify_control", {"action": "pause"}, ctx())
    assert result.status == "unverified" and fake.keys == ["play_pause"]
    assert "can't confirm" in result.message


def test_spotify_media_fallback_is_not_sent_when_spotify_is_closed():
    fake = FakeOS()
    result = registry(fake).execute("spotify_control", {"action": "pause"}, ctx())
    assert result.status == "needs_authorization" and fake.keys == []
    assert result.data["reason"] == "spotify_not_running"


def test_spotify_schema_requires_a_query_only_where_it_makes_sense():
    reg = registry(FakeOS())
    assert reg.execute("spotify_control", {"action": "play_playlist"}, ctx()).data["reason"] == "invalid_arguments"
    assert reg.execute("spotify_control", {"action": "pause", "query": "x"}, ctx()).data["reason"] == "invalid_arguments"
    assert reg.execute("spotify_control", {"action": "format_disk"}, ctx()).data["reason"] == "invalid_arguments"


def test_open_app_only_accepts_allowlisted_apps_and_verifies_the_process():
    fake = FakeOS(processes={"calculatorapp.exe"})
    reg = registry(fake)
    ok = reg.execute("open_app", {"app": "calculator"}, ctx())
    assert ok.status == "succeeded" and ok.data["verified"] is True
    assert fake.launched == ["calculator:"]
    refused = reg.execute("open_app", {"app": "cmd"}, ctx())
    assert refused.status == "denied" and refused.data["error"] == "application_not_approved"
    assert fake.launched == ["calculator:"]


def test_word_alias_resolves_to_the_approved_start_menu_application():
    fake = FakeOS(processes={"winword.exe"})
    def resolve(target, _name, _aliases):
        return target, "start_menu", None
    result = registry(fake, app_resolver=resolve).execute("open_app", {"app": "MS Word"}, ctx())
    assert result.status == "succeeded"
    assert result.data == {"requested_application": "MS Word", "resolved_application": "microsoft word",
                           "resolution_method": "start_menu", "verified": True, "execution_status": "started"}
    assert fake.launched == ["winword.exe"]


def test_ambiguous_alias_requests_a_clarification_and_missing_app_is_truthful():
    apps = {"notepad": App("notepad.exe", "notepad.exe", ("notes",)),
            "onenote": App("onenote.exe", "onenote.exe", ("notes",))}
    fake = FakeOS()
    reg = registry(fake, apps=apps, app_resolver=lambda *_: (None, "not_found", "Not installed."))
    ambiguous = reg.execute("open_app", {"app": "notes"}, ctx())
    missing = reg.execute("open_app", {"app": "Notepad"}, ctx())
    assert ambiguous.status == "needs_input" and "Which app did you mean" in ambiguous.message
    assert missing.status == "unavailable" and missing.data["execution_status"] == "not_started"
    assert fake.launched == []


def test_open_app_reports_launch_failure_and_unconfirmed_starts():
    assert registry(FakeOS(launch_ok=False)).execute("open_app", {"app": "notepad"}, ctx()).status == "failed"
    unconfirmed = registry(FakeOS()).execute("open_app", {"app": "notepad"}, ToolContext(deadline=time.monotonic() + 1.5))
    assert unconfirmed.status == "unverified"


def test_windows_launch_error_is_returned_without_claiming_success():
    result = registry(FakeOS(), app_launcher=lambda _target: (False, 1312)).execute(
        "open_app", {"app": "notepad"}, ctx())
    assert result.status == "failed"
    assert result.data["error"] == "winerror_1312"
    assert "no interactive logon session" in result.message


def test_owner_configured_apps_are_parsed_and_invalid_entries_ignored(tmp_path):
    exe = tmp_path / "Tool.exe"
    exe.write_bytes(b"MZ")
    apps = parse_apps(f"my tool={exe}|tool.exe;bad entry;shell=cmd.exe /c del;web=https://example.com")
    assert apps["my tool"].target == str(exe) and apps["my tool"].process == "tool.exe"
    assert apps["my tool"].aliases == ()
    assert "shell" not in apps and "web" not in apps and "bad entry" not in apps


def test_owner_can_configure_aliases_without_adding_executable_arguments(tmp_path):
    exe = tmp_path / "Editor.exe"
    exe.write_bytes(b"MZ")
    apps = parse_apps(f"code editor={exe}|Editor.exe|vscode,visual studio code")
    assert apps["code editor"].aliases == ("vscode", "visual studio code")
    assert desktop_os.resolve_application(str(exe), "code editor")[1] == "configured_path"
    assert "shell" not in parse_apps("shell=cmd.exe /c del")


def test_open_website_allows_only_owner_listed_hosts():
    fake = FakeOS()
    reg = registry(fake)
    assert reg.execute("open_website", {"url": "https://github.com/openai"}, ctx()).status == "succeeded"
    assert reg.execute("open_website", {"url": "http://localhost:5173/"}, ctx()).status == "succeeded"
    assert reg.execute("open_website", {"url": "https://docs.github.com/en"}, ctx()).status == "succeeded"
    for url in ("https://evil.example/?q=secret", "https://github.com.evil.example/", "javascript:alert(1)",
                "file:///C:/Windows/System32/cmd.exe", "https://user:pw@github.com/"):
        assert reg.execute("open_website", {"url": url}, ctx()).status == "denied", url
    assert fake.launched == ["https://github.com/openai", "http://localhost:5173/", "https://docs.github.com/en"]


def test_open_file_is_confined_to_the_attached_project_and_never_opens_executables(tmp_path):
    fake = FakeOS()
    reg = registry(fake)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.md").write_text("# plan", encoding="utf-8")
    (tmp_path / "run.bat").write_text("echo hi", encoding="utf-8")
    (tmp_path / "script.js").write_text("WScript.Echo(1)", encoding="utf-8")
    assert reg.execute("open_file", {"path": "docs/plan.md"}, ctx()).status == "unavailable"  # no project attached
    project = ctx(workspace_root=tmp_path, project_id=1)
    assert reg.execute("open_file", {"path": "docs/plan.md"}, project).status == "succeeded"
    for path in ("run.bat", "script.js", "../outside.txt", "docs/missing.md"):
        result = reg.execute("open_file", {"path": path}, ctx(workspace_root=tmp_path, project_id=1))
        assert result.status in ("denied", "unavailable"), path
    assert fake.launched == [str((tmp_path / "docs" / "plan.md").resolve())]


def test_open_project_in_vscode_uses_the_workspace_folder(tmp_path):
    fake = FakeOS(processes={"code.exe"})
    result = registry(fake).execute("open_project", {"app": "vscode"}, ctx(workspace_root=tmp_path, project_id=1))
    assert result.status == "succeeded"
    assert fake.launched == [f"vscode://file/{tmp_path.resolve().as_posix()}/"]


def test_media_keys_are_reported_as_unverified():
    fake = FakeOS()
    result = registry(fake).execute("media_control", {"action": "volume_down"}, ctx())
    assert result.status == "unverified" and fake.keys == ["volume_down"]


def test_media_key_rejection_returns_recovery_instructions():
    fake = FakeOS()
    fake.media_key = lambda _name: False
    result = registry(fake).execute("media_control", {"action": "play_pause"}, ctx())
    assert result.status == "failed" and result.data["reason"] == "os_rejected"
    assert "media player is open" in result.message


def test_project_commands_are_approval_only_and_checked_against_the_allowlist():
    reg = registry(FakeOS())
    args = {"argv": ["python", "-m", "pytest", "-q"], "reason": "run the tests"}
    assert reg.permission_for("run_project_command", args) == PermissionLevel.APPROVAL
    assert reg.execute("run_project_command", args, ctx(project_id=1)).status == "denied"
    tool = reg.get("run_project_command")
    assert tool is not None and tool.approval is not None
    parsed = tool.args.model_validate(args)
    assert tool.approval(parsed, ctx(project_id=1)) == ("run_command", {"argv": ["python", "-m", "pytest", "-q"], "cwd": "."})
    with pytest.raises(Exception, match="not allowlisted"):
        tool.approval(tool.args.model_validate({"argv": ["powershell", "-c", "x"], "reason": "r"}), ctx(project_id=1))


def test_disabled_desktop_control_explains_how_to_enable_it():
    reg = registry(FakeOS(), enabled=False)
    for name in ("open_app", "open_website", "media_control"):
        result = reg.execute(name, {}, ctx())
        assert result.status == "unavailable" and "NOVA_DESKTOP_CONTROL_ENABLED" in result.message, name


def test_spotify_playback_does_not_need_desktop_control():
    """Spotify runs through the owner's account (Web API). Desktop control off only removes app launching and keys."""
    calls = []

    class Connected(NoTokens):
        def load(self):
            return {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600}

    def api(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/me/player"):
            return httpx.Response(200, json={"is_playing": True, "device": {"name": "PC"},
                                             "item": {"uri": "spotify:track:x", "name": "Song", "artists": [{"name": "A"}]}})
        return httpx.Response(204)

    fake = FakeOS(processes={"spotify.exe"})
    service = SpotifyService("client-id", 8000, Path("unused.bin"), launcher=fake.launch, processes=fake.processes,
                             transport=httpx.MockTransport(api), poll_interval=0.01)
    service.tokens = Connected()
    reg = registry(fake, enabled=False, spotify=service)
    assert [t.name for t in reg.enabled_tools()] == ["spotify_control"]  # every desktop tool stays off

    playing = reg.execute("spotify_control", {"action": "now_playing"}, ctx())
    assert playing.status == "succeeded" and playing.message == "Spotify is playing Song by A on PC."
    assert ("GET", "/v1/me/player") in calls

    opened = reg.execute("spotify_control", {"action": "open"}, ctx())
    assert opened.status == "unavailable" and "NOVA_DESKTOP_CONTROL_ENABLED" in opened.message
    assert fake.launched == [] and fake.keys == []


def test_spotify_without_desktop_control_never_falls_back_to_media_keys():
    fake = FakeOS(processes={"spotify.exe"})
    result = registry(fake, enabled=False).execute("spotify_control", {"action": "pause"}, ctx())
    assert result.status == "needs_authorization" and result.data["connect_url"] == "/api/spotify/connect"
    assert fake.keys == [] and fake.launched == []


def test_spotify_is_off_only_when_it_is_not_set_up_and_desktop_control_is_off():
    unconfigured = SpotifyService("", 8000, Path("unused.bin"))
    result = registry(FakeOS(), enabled=False, spotify=unconfigured).execute("spotify_control", {"action": "pause"}, ctx())
    assert result.status == "unavailable" and "NOVA_SPOTIFY_CLIENT_ID" in result.message
    unconfigured.close()
