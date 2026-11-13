"""Application integrations that do work inside an application, starting with Microsoft Word documents.

Opening an application (`open_app`) only starts it. An *application integration* carries out a task in it and
verifies the outcome. Every integration follows the same shape (`ApplicationAdapter`): it says whether it is
available, reports its state read-only, and exposes typed actions that return NOVA's usual
`(status, message, data)` result, where "succeeded" means the outcome was checked, not that a request was sent.
`SpotifyService` (Web API) and `BrowserAdapter` (isolated browser) already work this way; a new application is one
more adapter plus its tool, registered in `app.main`.

Word documents are written as .docx files with python-docx and then opened in Word, instead of driving Word's
window. The file is the result the owner asked for, it can be read back and compared with what was asked, it does
not depend on which Word window has the focus or on a dialog being open, and it works while Word is busy.
What NOVA can then confirm about Word itself is that a Word window shows the document.

Boundaries: documents are created, read and opened only inside one folder (`NOVA_DOCUMENTS_DIR`, by default
Documents\\NOVA). File names are validated, an existing file is never replaced without the owner's approval on
screen, and nothing is reported as saved before the saved file has been read back.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.core.logging import get_logger
from app.tools import desktop_os
from app.tools.registry import PermissionLevel, Tool, ToolContext, ToolInput

log = get_logger("nova.documents")

DISABLED = "Desktop control is off. Set NOVA_DESKTOP_CONTROL_ENABLED=true in .env and restart NOVA."
NOT_WINDOWS = "Word documents are only available on Windows in this build."
NO_LIBRARY = "The python-docx package is not installed (pip install -r backend/requirements.txt)."
MAX_CONTENT = 20000
MAX_READ = 6000
# A cold start of Word showed the document after more than 15 s on the owner's laptop (live test, 2026-11-13; a warm
# start took 6 s). Giving up earlier reports a document that does open as "not seen". Bounded by the tool's deadline.
OPEN_WAIT_SECONDS = 30.0
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

Result = tuple[str, str, dict[str, Any]]
# ("heading", (level, text)) | ("paragraph", text) | ("bullet", text) | ("number", text) | ("table", rows)
Block = tuple[str, Any]


class ApplicationAdapter(Protocol):
    """What every application integration provides. Actions are methods returning `(status, message, data)`."""

    name: str

    def available(self) -> tuple[bool, str | None]:
        """Whether the integration can work now, and why not."""

    def state(self) -> dict[str, Any]:
        """A read-only description of the application: installed, running, what it has open."""


# ---- content --------------------------------------------------------------------------------------------------
def parse_blocks(text: str) -> list[Block]:
    """Plain text with light Markdown (# headings, - bullets, 1. numbered items, | tables |) as document blocks."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    table: list[list[str]] = []

    def end_paragraph() -> None:
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph)))
            paragraph.clear()

    def end_table() -> None:
        if table:
            blocks.append(("table", [list(row) for row in table]))
            table.clear()

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("|") and line.count("|") >= 2:
            end_paragraph()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):  # the |---|---| separator row
                table.append(cells)
            continue
        end_table()
        if not line:
            end_paragraph()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        bullet = re.match(r"^[-*•]\s+(.+)$", line)
        number = re.match(r"^\d{1,3}[.)]\s+(.+)$", line)
        if not (heading or bullet or number):
            paragraph.append(line)  # consecutive plain lines are one paragraph
            continue
        end_paragraph()
        if heading:
            blocks.append(("heading", (len(heading.group(1)), heading.group(2).strip())))
        elif bullet:
            blocks.append(("bullet", bullet.group(1).strip()))
        elif number:
            blocks.append(("number", number.group(1).strip()))
    end_paragraph()
    end_table()
    return blocks


_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*)")


def _runs(text: str) -> list[tuple[str, bool, bool]]:
    """(text, bold, italic) pieces of a line with **bold** and *italic* markers."""
    runs: list[tuple[str, bool, bool]] = []
    for piece in _INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            runs.append((piece[2:-2], True, False))
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            runs.append((piece[1:-1], False, True))
        else:
            runs.append((piece, False, False))
    return runs


def _plain(text: str) -> str:
    return " ".join("".join(t for t, _, _ in _runs(text)).split())


def expected_text(title: str | None, blocks: list[Block]) -> list[str]:
    """Every piece of text the saved document must contain."""
    wanted = [_plain(title)] if title else []
    for kind, value in blocks:
        if kind == "heading":
            wanted.append(_plain(value[1]))
        elif kind == "table":
            wanted.extend(_plain(cell) for row in value for cell in row)
        else:
            wanted.append(_plain(value))
    return [w for w in wanted if w]


def build_document(title: str | None, blocks: list[Block]) -> Any:
    from docx import Document

    document = Document()
    if title:
        document.add_heading(_plain(title), level=0)
        document.core_properties.title = _plain(title)[:255]
    document.core_properties.author = "NOVA"

    def add(style: str | None, text: str) -> None:
        paragraph = document.add_paragraph(style=style)
        for piece, bold, italic in _runs(text):
            run = paragraph.add_run(piece)
            run.bold = bold or None
            run.italic = italic or None

    for kind, value in blocks:
        if kind == "heading":
            document.add_heading(_plain(value[1]), level=value[0])
        elif kind == "bullet":
            add("List Bullet", value)
        elif kind == "number":
            add("List Number", value)
        elif kind == "table":
            columns = max(len(row) for row in value)
            grid = document.add_table(rows=len(value), cols=columns)
            grid.style = "Table Grid"
            for r, row in enumerate(value):
                for c in range(columns):
                    cell = grid.cell(r, c)
                    run = cell.paragraphs[0].add_run(_plain(row[c]) if c < len(row) else "")
                    run.bold = True if r == 0 else None  # the first row is the header
        else:
            add(None, value)
    return document


def read_document(path: Path) -> dict[str, Any]:
    """The text of a saved .docx, in document order."""
    from docx import Document
    from docx.table import Table

    document = Document(str(path))
    lines: list[str] = []
    paragraphs = tables = 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            tables += 1
            for row in item.rows:
                lines.append(" | ".join(" ".join(cell.text.split()) for cell in row.cells))
        else:
            text = " ".join(item.text.split())
            if text:
                paragraphs += 1
                lines.append(text)
    return {"lines": lines, "paragraphs": paragraphs, "tables": tables}


def clean_filename(name: str) -> str | None:
    """A safe .docx file name (no folders), or None when the name cannot be used."""
    name = name.strip()
    if not name or _INVALID_NAME.search(name) or name in (".", "..") or ".." in name:
        return None
    stem = name[:-5] if name.lower().endswith(".docx") else name
    stem = " ".join(stem.split()).rstrip(". ")
    if not stem or stem.casefold() in _RESERVED or stem.startswith("."):
        return None
    return stem[:120] + ".docx"


def _name_from_title(title: str) -> str:
    stem = " ".join(_INVALID_NAME.sub(" ", title).replace("..", " ").split()).strip(". ")
    return clean_filename(stem[:120] or "Document") or "Document.docx"


# ---- Word -----------------------------------------------------------------------------------------------------
class WordDocuments:
    """Microsoft Word documents in NOVA's document folder."""

    name = "microsoft word"

    def __init__(self, root: Path, *, launcher: Callable[[str], bool] = desktop_os.launch,
                 processes: Callable[[], set[str]] = desktop_os.running_processes,
                 windows: Callable[[], list[str]] = desktop_os.window_titles,
                 resolver: Callable[[str, str, tuple[str, ...]],
                                    tuple[str | None, str, str | None]] = desktop_os.resolve_application,
                 poll_interval: float = 0.5) -> None:
        self.root = root
        self.launcher = launcher
        self.processes = processes
        self.windows = windows
        self.resolver = resolver
        self.poll_interval = poll_interval

    # ---- adapter ----------------------------------------------------------------------------------------------
    def available(self) -> tuple[bool, str | None]:
        try:
            import docx  # noqa: F401
        except ImportError:
            return False, NO_LIBRARY
        return True, None

    def installed(self) -> bool:
        return self.resolver("winword.exe", "microsoft word", ("word",))[0] is not None

    def word_windows(self) -> list[str]:
        return [t for t in self.windows() if t == "Word" or t.endswith(" - Word")]

    def state(self) -> dict[str, Any]:
        documents: list[str] = []
        try:
            if self.root.is_dir():
                files = [p for p in self.root.glob("*.docx") if not p.name.startswith(("~", "."))]
                documents = [p.name for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:20]]
        except OSError:
            documents = []
        return {"installed": self.installed(), "running": "winword.exe" in self.processes(),
                "windows": self.word_windows(), "folder": str(self.root), "documents": documents}

    # ---- actions ----------------------------------------------------------------------------------------------
    def status(self) -> Result:
        state = self.state()
        if not state["installed"]:
            word = "Microsoft Word is not installed on this PC"
        elif state["running"]:
            titles = ", ".join(state["windows"]) or "no visible window"
            word = f"Word is running ({titles})"
        else:
            word = "Word is installed and not running"
        count = len(state["documents"])
        return ("succeeded", f"{word}. NOVA's document folder {state['folder']} has {count} document"
                f"{'' if count == 1 else 's'}{': ' + ', '.join(state['documents'][:5]) if count else ''}.",
                {"verified": True, **state})

    def create(self, title: str | None, content: str, filename: str | None, *, replace: bool,
               ctx: ToolContext) -> Result:
        """Write a new document, save it, read the saved file back and compare it with what was asked."""
        blocks = parse_blocks(content)
        if not blocks:
            return "denied", "There is no text to put in the document.", {"reason": "invalid_arguments", "verified": False}
        name = clean_filename(filename) if filename else _name_from_title(title or "Document")
        if name is None:
            return ("denied", f"{filename!r} is not a usable file name. Give a plain name without folders, for example "
                    "“Report.docx”; documents are saved in NOVA's document folder.",
                    {"reason": "path_rejected", "verified": False})
        wanted = expected_text(title, blocks)
        temporary = self.root / f".{secrets.token_hex(6)}.tmp"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            ctx.check()
            build_document(title, blocks).save(str(temporary))
            target = self._place(temporary, name, replace)
        except PermissionError:
            temporary.unlink(missing_ok=True)
            return ("failed", f"Windows denied access while saving {name} in {self.root}. If the document is open in "
                    "Word, close it there and ask again.", {"blocker": "access_denied", "verified": False,
                                                           "folder": str(self.root)})
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            log.warning("Saving a document failed", extra={"extra_data": {"error": type(exc).__name__,
                                                                           "winerror": getattr(exc, "winerror", None)}})
            return ("failed", f"The document could not be saved in {self.root} (Windows reported an error). Nothing was "
                    "written.", {"blocker": "save_failed", "verified": False, "folder": str(self.root)})
        checked = self._verify(target, wanted)
        counts = {k: sum(1 for kind, _ in blocks if kind == k) for k in ("heading", "paragraph", "bullet", "number", "table")}
        data = {"path": str(target), "filename": target.name, "folder": str(self.root), "replaced": replace,
                "renamed": target.name != name, "blocks": counts, **checked}
        if not checked["verified"]:
            return ("failed", f"The file {target} was written, but reading it back did not show the expected content "
                    f"({checked['missing']} of {len(wanted)} parts missing). Do not rely on it.",
                    {**data, "blocker": "verification_failed"})
        note = f" A document named {name} already existed, so this one has a new name." if data["renamed"] else ""
        return ("succeeded", f"Saved the document as “{target.name}” in {self.root} and checked that the saved file "
                f"contains the text ({checked['paragraphs']} paragraphs, {checked['tables']} table"
                f"{'' if checked['tables'] == 1 else 's'}).{note}", data)

    def _place(self, temporary: Path, name: str, replace: bool) -> Path:
        """Move the finished file to its name. Never replaces an existing file unless `replace` was approved."""
        if replace:
            target = self.root / name
            os.replace(temporary, target)
            return target
        stem = name[:-5]
        for number in range(1, 200):
            target = self.root / (name if number == 1 else f"{stem} ({number}).docx")
            if target.exists():
                continue
            try:
                # Hard-link then unlink: creating the name fails if it exists, so nothing is ever overwritten,
                # even if another program creates the same name at the same moment.
                os.link(temporary, target)
            except FileExistsError:
                continue
            except PermissionError:
                raise
            except OSError:
                if sys.platform != "win32":
                    raise
                os.rename(temporary, target)  # no hard links here; on Windows a rename also refuses to overwrite
                return target
            temporary.unlink(missing_ok=True)
            return target
        raise OSError("no free file name")

    @staticmethod
    def _verify(target: Path, wanted: list[str]) -> dict[str, Any]:
        try:
            saved = read_document(target)
            size = target.stat().st_size
        except Exception:  # noqa: BLE001 - an unreadable file is a failed verification, whatever the reason
            return {"verified": False, "missing": len(wanted), "paragraphs": 0, "tables": 0, "bytes": 0}
        present = {" ".join(part.split()) for line in saved["lines"] for part in (line, *line.split(" | "))}
        missing = sum(1 for w in wanted if w not in present)
        return {"verified": missing == 0 and size > 0, "missing": missing, "paragraphs": saved["paragraphs"],
                "tables": saved["tables"], "bytes": size}

    def _existing(self, filename: str) -> tuple[Path | None, Result | None]:
        name = clean_filename(filename)
        if name is None:
            return None, ("denied", f"{filename!r} is not a usable file name. NOVA only works with documents in "
                          f"{self.root}.", {"reason": "path_rejected", "verified": False})
        target = self.root / name
        if not target.is_file():
            return None, ("unavailable", f"There is no document named {name} in {self.root}.",
                          {"blocker": "not_found", "verified": False, "folder": str(self.root)})
        return target, None

    def read(self, filename: str) -> Result:
        target, problem = self._existing(filename)
        if target is None:
            assert problem is not None
            return problem
        try:
            saved = read_document(target)
        except PermissionError:
            return "failed", f"Windows denied access to {target}.", {"blocker": "access_denied", "verified": False}
        except Exception:  # noqa: BLE001 - not a readable .docx
            return "failed", f"{target.name} could not be read as a Word document.", {"blocker": "unreadable", "verified": False}
        text = "\n".join(saved["lines"])
        return ("succeeded", f"Read {target.name}: {saved['paragraphs']} paragraphs, {saved['tables']} table"
                f"{'' if saved['tables'] == 1 else 's'}.",
                {"verified": True, "path": str(target), "title": target.name, "text": text[:MAX_READ],
                 "truncated": len(text) > MAX_READ})

    def open(self, filename: str, ctx: ToolContext) -> Result:
        """Show a saved document in Word and confirm that a Word window has it."""
        target, problem = self._existing(filename)
        if target is None:
            assert problem is not None
            return problem
        data: dict[str, Any] = {"path": str(target), "filename": target.name, "verified": False}
        if not self.installed():
            return ("unavailable", f"Microsoft Word is not installed on this PC, so the document was not opened. It is "
                    f"saved at {target}.", {**data, "blocker": "not_installed"})
        data["word_was_running"] = "winword.exe" in self.processes()
        if self._shown(target):
            return "succeeded", f"“{target.name}” is already open in Word.", {**data, "verified": True}
        if not self.launcher(str(target)):
            return ("failed", f"Windows could not open {target.name}. It is saved at {target}.",
                    {**data, "blocker": "launch_failed"})
        until = min(ctx.deadline - 0.5, time.monotonic() + OPEN_WAIT_SECONDS)
        while time.monotonic() + self.poll_interval < until:
            ctx.sleep(self.poll_interval)
            if self._shown(target):
                return "succeeded", f"Opened “{target.name}” in Word.", {**data, "verified": True}
        return ("unverified", f"Asked Windows to open {target.name}, but NOVA could not see it in a Word window. It is "
                f"saved at {target}.", {**data, "blocker": "window_not_seen"})

    def _shown(self, target: Path) -> bool:
        stem = target.stem.casefold()
        return any(stem in title.casefold() for title in self.word_windows())


# ---- tool -----------------------------------------------------------------------------------------------------
_ACTION_ALIASES = {"write": "create", "new": "create", "save": "create", "create_document": "create",
                   "new_document": "create", "write_document": "create", "show": "open", "open_document": "open",
                   "read_document": "read", "state": "status", "list": "status", "info": "status"}
_ABSENT = ("", "null", "none", "undefined", "n/a")


class WordDocumentInput(ToolInput):
    action: Literal["create", "open", "read", "status"]
    title: str | None = Field(default=None, min_length=1, max_length=150, description="Document title, for create.")
    content: str | None = Field(default=None, min_length=1, max_length=MAX_CONTENT, description=(
        "The complete text to write, for create. One item per line: '# Heading', '## Subheading', '- bullet', "
        "'1. numbered item', '| cell | cell |' table rows, **bold**. Plain lines are paragraphs."))
    filename: str | None = Field(default=None, min_length=1, max_length=125, description=(
        "File name without folders, for example 'Report.docx'. Required for open and read; optional for create."))
    if_exists: Literal["new_name", "replace"] = Field(default="new_name", description=(
        "For create when the file name is taken: 'new_name' keeps both (default); 'replace' needs the owner's approval."))
    open_in_word: bool = Field(default=True, description="For create: also show the saved document in Word.")

    @model_validator(mode="before")
    @classmethod
    def normalise(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = {k: v for k, v in data.items()
                if not (k in ("title", "content", "filename") and (v is None or str(v).strip().lower() in _ABSENT))}
        action = str(data.get("action", "")).strip().lower().replace(" ", "_")
        return {**data, "action": _ACTION_ALIASES.get(action, action)}

    @model_validator(mode="after")
    def required_fields(self) -> WordDocumentInput:
        if self.action == "create" and not self.content:
            raise ValueError("create requires content: the complete text of the document.")
        if self.action in ("open", "read") and not self.filename:
            raise ValueError(f"{self.action} requires filename.")
        return self


def document_tools(enabled: bool, word: WordDocuments) -> list[Tool]:
    usable, missing = word.available()
    on = enabled and sys.platform == "win32" and usable
    reason = DISABLED if not enabled else NOT_WINDOWS if sys.platform != "win32" else missing

    def handler(args: WordDocumentInput, ctx: ToolContext) -> Result:
        if args.action == "status":
            return word.status()
        if args.action == "read":
            return word.read(args.filename or "")
        if args.action == "open":
            return word.open(args.filename or "", ctx)
        status, message, data = word.create(args.title, args.content or "", args.filename,
                                            replace=args.if_exists == "replace", ctx=ctx)
        if status != "succeeded" or not args.open_in_word:
            return status, message, data
        opened, open_message, open_data = word.open(data["filename"], ctx)
        # The document exists and was checked: that part succeeded whatever Word did afterwards.
        return status, f"{message} {open_message}", {**data, "opened": opened == "succeeded",
                                                     "open_status": opened, "open_blocker": open_data.get("blocker")}

    def classify(args: WordDocumentInput) -> PermissionLevel:
        if args.action in ("status", "read"):
            return PermissionLevel.READ
        if args.action == "create" and args.if_exists == "replace":
            return PermissionLevel.APPROVAL  # replacing a file cannot be undone: the owner confirms on screen
        return PermissionLevel.ROUTINE

    return [
        Tool("word_document",
             "Work with Microsoft Word documents in NOVA's document folder: create (write the given text with headings, "
             "lists and tables into a new .docx, save it, verify the saved file, show it in Word), open an existing "
             "document in Word, read a document's text, or status (is Word installed/running, which documents exist). "
             "Use this whenever the user wants something written, reported or saved in Word; open_app only starts "
             "Word and writes nothing.",
             WordDocumentInput, PermissionLevel.READ, on, handler, timeout_s=40, unavailable_reason=reason,
             classify=classify),
    ]
