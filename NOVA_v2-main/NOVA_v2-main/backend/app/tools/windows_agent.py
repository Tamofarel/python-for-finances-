"""Narrow Windows desktop actions. No shell, free-form executable, arguments or arbitrary file access.

Least privilege by construction: each tool accepts only typed, validated arguments, launches only targets from an
owner-configured allowlist (apps, website hosts) or files inside the conversation's project workspace, and never
passes arguments to a launched program. Commands are never run here: `run_project_command` only queues an approval
request for an allowlisted command, which the existing approval flow runs after the owner confirms it on screen.

These run inside NOVA's backend process, which already runs locally as the signed-in Windows user. A separate agent
process under the same account would add no isolation, so the allowlists are the boundary (see docs/ACTIONS.md).
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, create_model, model_validator

from app.core.errors import AppError
from app.tools import desktop_os
from app.tools.browser import BrowserAdapter
from app.tools.commands import validate_argv
from app.tools.registry import PermissionLevel, Tool, ToolContext, ToolInput, ToolRegistry
from app.tools.spotify import SpotifyService
from app.workspaces import Workspace

DISABLED = "Desktop control is off. Set NOVA_DESKTOP_CONTROL_ENABLED=true in .env and restart NOVA."
NOT_WINDOWS = "Desktop control is only available on Windows in this build."
BROWSER_UNAVAILABLE = "Install backend/requirements-browser.txt and the Playwright Chromium browser to enable this tool."

# Files are opened with their default app, so only formats whose default app does not execute code are allowed.
SAFE_EXTENSIONS = frozenset({
    ".txt", ".md", ".log", ".csv", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".mp3", ".wav", ".flac", ".m4a", ".mp4", ".mov", ".mkv", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
})
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:[^\s]*$", re.IGNORECASE)
_LAUNCHABLE_SUFFIXES = (".exe", ".lnk", ".appref-ms")


@dataclass(frozen=True)
class App:
    target: str  # a URI, executable name, or configured path to an .exe / shortcut
    process: str | None  # executable name used to confirm that the app started
    aliases: tuple[str, ...] = ()


DEFAULT_APPS: dict[str, App] = {
    "spotify": App("spotify:", "spotify.exe", ("spotify app",)),
    "calculator": App("calculator:", "calculatorapp.exe", ("calc", "calculatrice")),
    "settings": App("ms-settings:", "systemsettings.exe", ("windows settings", "parametres")),
    "notepad": App("notepad.exe", "notepad.exe", ("notes", "note pad", "bloc notes")),
    "file explorer": App("explorer.exe", None, ("explorer", "windows explorer", "file manager")),
    "microsoft word": App("winword.exe", "winword.exe", ("word", "ms word", "microsoft office word")),
    "vscode": App("code.exe", "code.exe", ("vs code", "visual studio code", "code editor")),
}


def _app_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def app_aliases(apps: dict[str, App]) -> tuple[dict[str, str], set[str]]:
    """Return unambiguous aliases and aliases that need a clarification."""
    seen: dict[str, set[str]] = {}
    for name, app in apps.items():
        for alias in (name, *app.aliases):
            seen.setdefault(_app_key(alias), set()).add(name)
    ambiguous = {alias for alias, names in seen.items() if len(names) > 1}
    return ({alias: next(iter(names)) for alias, names in seen.items() if len(names) == 1}, ambiguous)


def parse_apps(spec: str) -> dict[str, App]:
    """Owner-configured apps: "name=target|process.exe|alias1,alias2;...". Invalid entries are ignored."""
    apps: dict[str, App] = {}
    for entry in spec.split(";"):
        name, _, rest = entry.partition("=")
        name = " ".join(name.lower().split())
        pieces = rest.strip().split("|", 2)
        target = pieces[0]
        process = pieces[1] if len(pieces) > 1 else ""
        aliases = tuple(" ".join(a.casefold().split()) for a in pieces[2].split(",") if a.strip()) if len(pieces) > 2 else ()
        target = target.strip()
        if not name or not target or not re.fullmatch(r"[a-z0-9 ._-]{1,40}", name):
            continue
        if target.lower().startswith(("http:", "https:", "file:")) or " " in target and not Path(target).is_file():
            continue  # websites go through open_website; no arguments may be smuggled in
        is_uri = bool(_SCHEME.match(target)) and not Path(target).drive
        is_path = Path(target).is_absolute() and target.lower().endswith(_LAUNCHABLE_SUFFIXES) and Path(target).is_file()
        is_executable = bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}\.exe", target, re.IGNORECASE))
        is_program = is_path or is_executable
        if not (is_uri or is_program):
            continue
        if any(not re.fullmatch(r"[a-z0-9 ._-]{1,40}", alias) for alias in aliases):
            continue
        apps[name] = App(target, process.strip().lower() or (Path(target).name.lower() if is_executable else None), aliases)
    return apps


def _hosts(spec: str) -> list[str]:
    return [h.strip().lower().lstrip(".") for h in spec.split(",") if h.strip()]


def host_allowed(url: str, hosts: list[str]) -> tuple[bool, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False, "Only http and https addresses can be opened."
    if parts.username or parts.password:
        return False, "Addresses with embedded credentials are not opened."
    host = parts.hostname.lower()
    if any(host == h or host.endswith("." + h) for h in hosts):
        return True, host
    return False, (f"{host} is not on your list of allowed websites (NOVA_BROWSER_ALLOWED_HOSTS). "
                   f"Allowed: {', '.join(hosts) or 'none'}.")


# ---- argument schemas -----------------------------------------------------------------------------------------
_SPOTIFY_ALIASES = {"play": "play_track", "play_song": "play_track", "play_music": "play_track", "song": "play_track",
                    "track": "play_track", "playlist": "play_playlist", "stop": "pause", "skip": "next",
                    "back": "previous", "unpause": "resume", "louder": "volume_up", "quieter": "volume_down",
                    "toggle": "play_pause", "current": "now_playing", "current_track": "now_playing",
                    "currently_playing": "now_playing", "what_is_playing": "now_playing", "status": "now_playing"}
SPOTIFY_OFF = ("Spotify is not set up (NOVA_SPOTIFY_CLIENT_ID is empty) and desktop control is off "
               "(NOVA_DESKTOP_CONTROL_ENABLED=false). Add your Spotify app's client ID to .env and restart NOVA.")


class SpotifyCommand(ToolInput):
    action: Literal["open", "search", "play_track", "play_playlist", "play_pause", "pause", "resume", "next",
                    "previous", "volume_up", "volume_down", "now_playing"]
    query: str | None = Field(default=None, min_length=1, max_length=240,
                              description="Track title (optionally with the artist), playlist name, or search text.")
    artist: str | None = Field(default=None, min_length=1, max_length=120, description="Artist name, for play_track.")
    uri: str | None = Field(default=None, pattern=r"^spotify:(track|playlist|album|artist):[A-Za-z0-9]{1,64}$",
                            description="Exact Spotify URI of an item NOVA listed earlier.")

    @model_validator(mode="before")
    @classmethod
    def normalise(cls, data: Any) -> Any:
        """Map common phrasings to the operation the owner asked for (small models often send "open" + a song)."""
        if not isinstance(data, dict):
            return data
        # Models sometimes fill unused fields with "null", "none" or "": treat those as absent.
        data = {k: v for k, v in data.items() if not (k in ("query", "artist", "uri") and (
            v is None or str(v).strip().lower() in ("", "null", "none", "undefined", "n/a")))}
        action = str(data.get("action", "")).strip().lower().replace(" ", "_")
        action = _SPOTIFY_ALIASES.get(action, action)
        if action == "open" and (data.get("query") or data.get("uri")):
            action = "play_track"  # "open Spotify and play X"
        if action == "play_track" and not data.get("query") and not data.get("uri"):
            action = "resume"  # "play" with nothing named
        return {**data, "action": action}

    @model_validator(mode="after")
    def query_for_search(self) -> SpotifyCommand:
        plays = self.action in ("play_track", "play_playlist")
        needs_query = self.action == "search" or (plays and self.uri is None)
        if needs_query and not self.query:
            raise ValueError(f"{self.action} requires a query.")
        if not (plays or self.action == "search") and self.query is not None:
            raise ValueError(f"{self.action} does not accept a query.")
        if self.artist is not None and self.action != "play_track":
            raise ValueError("artist is only used with play_track.")
        if self.uri is not None and not plays:
            raise ValueError("uri is only used with play_track or play_playlist.")
        return self


class WebsiteInput(ToolInput):
    url: str = Field(min_length=8, max_length=2000, description="An http(s) address on an allowed host.")


class BrowserURLInput(ToolInput):
    url: str = Field(min_length=9, max_length=2000, pattern=r"(?i)^https://",
                     description="A public HTTPS address on an approved host.")


class BrowserSearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200, description="Text to find on the current approved page.")


class BrowserLinkInput(ToolInput):
    text: str = Field(min_length=1, max_length=160, description="Exact visible text of a link on the current page.")


class FileInput(ToolInput):
    path: str = Field(min_length=1, max_length=400, description="Path relative to the attached project's folder.")


class ProjectInput(ToolInput):
    app: Literal["vscode", "explorer"] = Field(description="Open the project folder in VS Code or File Explorer.")


class MediaInput(ToolInput):
    action: Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]


class CommandInput(ToolInput):
    argv: list[str] = Field(min_length=1, max_length=32, description="Command as an argument list, no shell.")
    reason: str = Field(min_length=1, max_length=300, description="Why the command should run.")


# ---- tools ------------------------------------------------------------------------------------------------------
def desktop_tools(enabled: bool, spotify: SpotifyService, *, apps: dict[str, App] | None = None,
                  browser_hosts: str = "", allowlisted_commands: list[list[str]] | None = None,
                  browser: BrowserAdapter | None = None,
                  launcher: Callable[[str], bool] = desktop_os.launch,
                  app_launcher: Callable[[str], tuple[bool, int | None]] | None = None,
                  app_resolver: Callable[[str, str, tuple[str, ...]],
                                         tuple[str | None, str, str | None]] = desktop_os.resolve_application,
                  processes: Callable[[], set[str]] = desktop_os.running_processes,
                  media_key: Callable[[str], bool] = desktop_os.send_media_key) -> list[Tool]:
    apps = dict(DEFAULT_APPS if apps is None else apps)
    if app_launcher is None:
        app_launcher = desktop_os.launch_detailed if launcher is desktop_os.launch else lambda target: (launcher(target), None)
    hosts = _hosts(browser_hosts)
    commands = allowlisted_commands or []
    on = enabled and sys.platform == "win32"
    reason = DISABLED if not enabled else NOT_WINDOWS
    # Spotify playback goes through the owner's Spotify account (Web API), so it does not need desktop control.
    # With desktop control off, NOVA never starts the app and never sends media keys for it.
    spotify_on = sys.platform == "win32" and (enabled or spotify.configured)
    spotify_reason = NOT_WINDOWS if sys.platform != "win32" else SPOTIFY_OFF

    def wait_for_process(name: str | None, ctx: ToolContext, seconds: float = 6.0) -> bool:
        if not name:
            return False
        until = min(ctx.deadline - 0.2, time.monotonic() + seconds)
        while True:
            if name in processes():
                return True
            if time.monotonic() + 0.5 >= until:
                return False
            ctx.sleep(0.5)

    def spotify_handler(args: SpotifyCommand, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        status, message, data = spotify.command(args.action, args.query, artist=args.artist, uri=args.uri, ctx=ctx,
                                                allow_launch=enabled)
        key = {"pause": "play_pause", "resume": "play_pause", "play_pause": "play_pause"}.get(args.action, args.action)
        if enabled and status == "needs_authorization" and key in desktop_os.MEDIA_KEYS:
            if "spotify.exe" not in processes():
                return (status, "Spotify is not connected and the Spotify app is not running. Open Spotify and connect it "
                        "in NOVA Settings for confirmed playback control.",
                        {"verified": False, "fallback": "not_attempted", "reason": "spotify_not_running", **data})
            if media_key(key):
                return ("unverified", "Spotify isn't connected to NOVA, so I sent the Windows media key instead; NOVA "
                        "can't confirm that Spotify responded. Connect Spotify in Settings → Voice for confirmed control.",
                        {"verified": False, "fallback": "media_key", **data})
            return ("failed", "Windows rejected the media key. Keep Spotify open, or connect Spotify in NOVA Settings "
                    "for confirmed control.",
                    {"verified": False, "fallback": "media_key_rejected", "reason": "os_rejected", **data})
        return status, message, data

    app_names = tuple(sorted(apps)) or ("none",)
    alias_map, ambiguous_aliases = app_aliases(apps)
    exposed_names = sorted({*app_names, *(alias for app in apps.values() for alias in app.aliases)})
    AppInput = create_model("AppInput", __base__=ToolInput,
                            app=(str, Field(min_length=1, max_length=80,
                                            description="Approved application name or alias: " + ", ".join(exposed_names))))

    def open_app(args: Any, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        requested = " ".join(args.app.split())
        requested_key = _app_key(requested)
        if requested_key in ambiguous_aliases:
            matches = [name for name, app_def in apps.items()
                       if requested_key in {_app_key(alias) for alias in (name, *app_def.aliases)}]
            return ("needs_input", f"Which app did you mean by {requested!r}: {' or '.join(matches)}?",
                    {"requested_application": requested, "execution_status": "not_started",
                     "error": "ambiguous_application", "candidates": matches})
        canonical = alias_map.get(requested_key)
        app = apps.get(canonical or "")
        if app is None:
            return ("denied", f"{requested!r} is not an approved application. Add it to NOVA_DESKTOP_APPS to allow it.",
                    {"requested_application": requested, "execution_status": "not_started",
                     "error": "application_not_approved", "verified": False})
        target, resolution, error = app_resolver(app.target, canonical or requested, app.aliases)
        base = {"requested_application": requested, "resolved_application": canonical,
                "resolution_method": resolution, "verified": False}
        if target is None:
            details = {**base, "execution_status": "not_started", "error": error}
            return "unavailable", error or f"Could not find {canonical}.", details
        launched, winerror = app_launcher(target)
        if not launched:
            reason = {
                2: "The application path no longer exists.",
                5: "Windows denied access to the application.",
                1155: "Windows has no application registered for this target.",
                1312: "Windows could not activate the app because this process has no interactive logon session.",
                193: "The configured target is not a valid Windows application.",
            }.get(winerror, "Windows refused the application activation.")
            error = f"Windows could not open {canonical}. {reason}"
            return "failed", error, {**base, "resolution_method": resolution,
                                     "execution_status": "launch_failed",
                                     "error": f"winerror_{winerror}" if winerror is not None else "launch_failed",
                                     "winerror": winerror}
        if wait_for_process(app.process, ctx):
            return "succeeded", f"Opened {canonical}.", {**base, "execution_status": "started", "verified": True}
        if app.process is None:
            return ("unverified", f"Windows accepted the request to open {canonical}; NOVA cannot confirm its window.",
                    {**base, "execution_status": "accepted_unconfirmed", "error": None})
        error = f"Windows accepted the request, but NOVA could not confirm that {canonical} started."
        return "unverified", error, {**base, "execution_status": "start_unconfirmed", "error": error}

    def open_website(args: WebsiteInput, _ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        ok, detail = host_allowed(args.url, hosts)
        if not ok:
            return "denied", detail, {"reason": "host_not_allowed"}
        if not launcher(args.url):
            return "unavailable", "Windows could not open your default browser.", {"verified": False}
        return ("succeeded", f"Opened {detail} in your default browser.",
                {"verified": False, "note": "NOVA cannot see inside the browser to confirm the page loaded."})

    def browser_open(args: BrowserURLInput, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return browser.open(ctx.owner_id or 0, args.url, ctx) if browser else (
            "unavailable", "The isolated browser adapter is not configured.", {"verified": False})

    def browser_inspect(_args: Any, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return browser.inspect(ctx.owner_id or 0, ctx) if browser else (
            "unavailable", "The isolated browser adapter is not configured.", {"verified": False})

    def browser_search(args: BrowserSearchInput, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return browser.search(ctx.owner_id or 0, args.query, ctx) if browser else (
            "unavailable", "The isolated browser adapter is not configured.", {"verified": False})

    def browser_link(args: BrowserLinkInput, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return browser.click_link(ctx.owner_id or 0, args.text, ctx) if browser else (
            "unavailable", "The isolated browser adapter is not configured.", {"verified": False})

    def workspace(ctx: ToolContext) -> Workspace | None:
        return Workspace(ctx.workspace_root) if ctx.workspace_root else None

    def open_file(args: FileInput, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        ws = workspace(ctx)
        if ws is None:
            return "unavailable", "Attach a project to this conversation first; NOVA only opens files inside a project.", {}
        try:
            _, target = ws.resolve(args.path)
        except AppError as exc:
            return "denied", exc.message, {"reason": "path_rejected"}
        if not target.is_file():
            return "unavailable", f"There is no file at {args.path} in this project.", {}
        if target.suffix.lower() not in SAFE_EXTENSIONS:
            return ("denied", f"NOVA does not open {target.suffix or 'extension-less'} files with their default app, "
                    "because that could run code. Open it in your editor instead.", {"reason": "unsafe_type"})
        if not launcher(str(target)):
            return "unavailable", "Windows has no app registered to open this file.", {"verified": False}
        return "succeeded", f"Opened {args.path}.", {"verified": False}

    def open_project(args: ProjectInput, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        ws = workspace(ctx)
        if ws is None:
            return "unavailable", "Attach a project to this conversation first.", {}
        if args.app == "vscode":
            if not launcher(f"vscode://file/{ws.root.as_posix()}/"):
                return "unavailable", "Windows could not open VS Code. Is it installed?", {"verified": False}
            if wait_for_process("code.exe", ctx):
                return "succeeded", "Opened the project in VS Code.", {"verified": True}
            return "unverified", "Asked Windows to open VS Code, but NOVA could not confirm that it started.", {"verified": False}
        if not launcher(str(ws.root)):
            return "unavailable", "Windows could not open File Explorer.", {"verified": False}
        return "succeeded", "Opened the project folder in File Explorer.", {"verified": False}

    def media(args: MediaInput, _ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        if not media_key(args.action):
            return ("failed", "Windows rejected the media key. Make sure a media player is open and try again.",
                    {"verified": False, "reason": "os_rejected", "recovery": "Open a media player or connect Spotify."})
        label = args.action.replace("_", " ")
        return ("unverified", f"Sent the {label} media key to Windows. NOVA can't confirm which app responded.",
                {"verified": False})

    def command_approval(args: CommandInput, ctx: ToolContext) -> tuple[str, dict[str, Any]]:
        if ctx.project_id is None:
            raise AppError("Attach a project to this conversation first; commands run in a project folder.",
                           code="project_required")
        return "run_command", {"argv": validate_argv(list(args.argv), commands), "cwd": "."}

    def never_directly(_args: Any, _ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return "denied", "Commands run only through the approval flow.", {"reason": "approval_required"}

    app_list = ", ".join(app_names)
    return [
        Tool("spotify_control",
             "Control Spotify on this PC with the owner's connected Spotify account: open the app, play a track "
             "(query = the title, artist = the artist when known), play one of the owner's playlists, pause, resume, "
             "skip, go back, change the volume, or report what is playing now (now_playing). Playback is confirmed "
             "from Spotify's reported state.",
             SpotifyCommand, PermissionLevel.ROUTINE, spotify_on, spotify_handler, timeout_s=45,
             unavailable_reason=spotify_reason),
        Tool("open_app", f"Open one of the owner's approved Windows applications ({app_list}).", AppInput,
             PermissionLevel.ROUTINE, on, open_app, timeout_s=15, unavailable_reason=reason),
        Tool("open_website", "Open a web page in the default browser. Only hosts the owner allowed can be opened "
             f"({', '.join(hosts) or 'none configured'}); local development servers on localhost are allowed when listed.",
             WebsiteInput, PermissionLevel.ROUTINE, on, open_website, timeout_s=10, unavailable_reason=reason),
        Tool("browser_open", "Open an approved public HTTPS website in NOVA's isolated visible browser and read its "
             "visible page.",
             BrowserURLInput, PermissionLevel.ROUTINE, on and browser is not None, browser_open, timeout_s=30,
             unavailable_reason=reason if not on else BROWSER_UNAVAILABLE),
        Tool("browser_inspect", "Read the title and visible text from NOVA's current isolated browser page.",
             ToolInput, PermissionLevel.READ, on and browser is not None, browser_inspect, timeout_s=12,
             unavailable_reason=reason if not on else BROWSER_UNAVAILABLE),
        Tool("browser_search", "Find matching visible text on NOVA's current isolated browser page.",
             BrowserSearchInput, PermissionLevel.READ, on and browser is not None, browser_search, timeout_s=12,
             unavailable_reason=reason if not on else BROWSER_UNAVAILABLE),
        Tool("browser_open_link", "Navigate to a visible link by its exact text on the current page, after URL policy checks.",
             BrowserLinkInput, PermissionLevel.ROUTINE, on and browser is not None, browser_link, timeout_s=30,
             unavailable_reason=reason if not on else BROWSER_UNAVAILABLE),
        Tool("open_file", "Open a document, image or media file from the attached project with its default app. "
             "Executables and scripts are never opened.", FileInput, PermissionLevel.ROUTINE, on, open_file,
             timeout_s=10, unavailable_reason=reason),
        Tool("open_project", "Open the attached project's folder in VS Code or File Explorer.", ProjectInput,
             PermissionLevel.ROUTINE, on, open_project, timeout_s=15, unavailable_reason=reason),
        Tool("media_control", "Send a system media key (play/pause, next, previous, volume up/down, mute) to whatever "
             "app is playing. Use spotify_control for Spotify.", MediaInput, PermissionLevel.ROUTINE, on, media,
             timeout_s=5, unavailable_reason=reason),
        Tool("run_project_command", "Ask the owner to approve running an allowlisted command (for example the test "
             "suite) in the attached project. It does not run until the owner approves it on screen.", CommandInput,
             PermissionLevel.APPROVAL, on and bool(commands), never_directly, timeout_s=5,
             unavailable_reason=reason if not on else "No commands are allowlisted (NOVA_COMMAND_ALLOWLIST).",
             approval=command_approval),
    ]


def build_windows_registry(enabled: bool = False, spotify: SpotifyService | None = None, **kwargs: Any) -> ToolRegistry:
    service = spotify or SpotifyService("", 8000, Path("spotify-token.bin"))
    return ToolRegistry(desktop_tools(enabled, service, **kwargs))
