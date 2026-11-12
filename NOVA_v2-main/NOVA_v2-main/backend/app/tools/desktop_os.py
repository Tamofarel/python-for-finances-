"""Thin Windows primitives used by the desktop tools.

No shell is used and no arguments are ever passed to a launched program: `launch` hands one fixed URI or path to
Windows, which opens it with the registered application (the same as double-clicking it).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}


def _normal_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def resolve_application(target: str, name: str, aliases: tuple[str, ...] = ()) -> tuple[str | None, str, str | None]:
    """Resolve an approved app to a configured path, PATH executable, Start Menu shortcut, or URI scheme."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) and not Path(target).drive:
        return target, "uri_scheme", None
    configured = Path(target).expanduser()
    if configured.is_absolute():
        if configured.is_file() and configured.suffix.lower() in (".exe", ".lnk", ".appref-ms"):
            return str(configured.resolve()), "configured_path", None
        return None, "configured_path", "The configured application path does not exist or is not launchable."
    executable = shutil.which(target)
    if executable:
        return str(Path(executable).resolve()), "path", None
    if sys.platform == "win32":
        roots = [Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                 for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")) if base]
        wanted = {_normal_name(v) for v in (name, *aliases) if v}
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for shortcut in root.rglob("*"):
                    if shortcut.is_file() and shortcut.suffix.lower() in (".lnk", ".appref-ms"):
                        stem = _normal_name(shortcut.stem)
                        if stem in wanted:
                            return str(shortcut.resolve()), "start_menu", None
            except OSError:
                continue
    return None, "not_found", f"{name} is not installed or has no Start Menu shortcut."


def launch(target: str) -> bool:
    """Open a URI (spotify:, ms-settings:, https:) or file with the Windows shell."""
    return launch_detailed(target)[0]


def launch_detailed(target: str) -> tuple[bool, int | None]:
    """Shell-activate a fixed URI/path and preserve Windows' error code for truthful tool results."""
    if sys.platform != "win32":
        return False, None
    try:
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - one fixed target, no shell, no arguments
    except OSError as exc:
        return False, getattr(exc, "winerror", None)
    return True, None


def running_processes() -> set[str]:
    """Lower-case executable names of the processes running now (empty when not on Windows)."""
    if sys.platform != "win32":
        return set()
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return set()
    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def send_media_key(name: str) -> bool:
    vk = MEDIA_KEYS.get(name)
    if vk is None or sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

    extra = ctypes.c_ulong(0)
    down = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, 0, 0, ctypes.pointer(extra))))
    up = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, 2, 0, ctypes.pointer(extra))))
    send = ctypes.windll.user32.SendInput
    return send(1, ctypes.byref(down), ctypes.sizeof(INPUT)) == 1 and send(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 1
