from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.api.projects import project_target
from app.core.errors import AppError
from app.core.state import AppState
from app.models import Artifact, ChangeProposal, User
from app.providers.base import ChatMessage, ProviderFailure
from app.schemas import (
    AIChangeIn,
    ApplyChangeIn,
    ArtifactOut,
    ChangeOut,
    CreatePathIn,
    FileContentOut,
    FileEntryOut,
    ProposeChangeIn,
    SummaryIn,
)
from app.services import audit, changes
from app.services.assistant import build_system_prompt, resolve_provider, untrusted
from app.workspaces import sha256_text

router = APIRouter(prefix="/api/projects/{project_id}", tags=["files"])

_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)


def _ctx(project_id: int, db: Session, user: User, state: AppState):  # type: ignore[no-untyped-def]
    proj = owned_project(project_id, db, user)
    return proj, changes.workspace_for(proj, state.settings.max_file_bytes)


@router.get("/files", response_model=list[FileEntryOut])
def list_files(project_id: int, path: str = Query(default="", max_length=1024), user: User = Depends(current_user),
               db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> list[FileEntryOut]:
    _, ws = _ctx(project_id, db, user, state)
    return [FileEntryOut(**e.__dict__) for e in ws.list_dir(path)]


@router.get("/files/content", response_model=FileContentOut)
def read_file(project_id: int, path: str = Query(min_length=1, max_length=1024), user: User = Depends(current_user),
              db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> FileContentOut:
    _, ws = _ctx(project_id, db, user, state)
    clean, text = ws.read_text(path)
    return FileContentOut(path=clean, content=text, sha256=sha256_text(text), size=len(text.encode()))


@router.post("/files", response_model=FileEntryOut, status_code=201)
def create_path(project_id: int, body: CreatePathIn, user: User = Depends(current_user),
                db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> FileEntryOut:
    """Create a NEW file or folder. Existing files are never overwritten here (use a change proposal)."""
    proj, ws = _ctx(project_id, db, user, state)
    if ws.exists(body.path):
        raise AppError("A file or folder with that name already exists.", code="exists", status_code=409)
    if body.type == "dir":
        clean = ws.create_dir(body.path)
    else:
        clean = ws.write_text(body.path, body.content)
    audit.record(db, f"file.created_{body.type}", user_id=user.id, target=project_target(proj.id, clean))
    parent = clean.rsplit("/", 1)[0] if "/" in clean else ""
    entry = next(e for e in ws.list_dir(parent) if e.path == clean)
    return FileEntryOut(**entry.__dict__)


# ---- change proposals ------------------------------------------------------------------
@router.get("/changes", response_model=list[ChangeOut])
def list_changes(project_id: int, status: str | None = Query(default=None, max_length=16),
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ChangeProposal]:
    owned_project(project_id, db, user)
    stmt = select(ChangeProposal).where(ChangeProposal.project_id == project_id)
    if status:
        stmt = stmt.where(ChangeProposal.status == status)
    return list(db.scalars(stmt.order_by(ChangeProposal.id.desc()).limit(200)))


@router.post("/changes", response_model=ChangeOut, status_code=201)
def propose_change(project_id: int, body: ProposeChangeIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ChangeProposal:
    proj, ws = _ctx(project_id, db, user, state)
    cp = changes.propose(db, proj, ws, path=body.path, content=body.content, summary=body.summary,
                         source="user", base_sha256=body.base_sha256)
    audit.record(db, "change.proposed", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id, "source": "user", "operation": cp.operation})
    return cp


@router.post("/changes/ai", response_model=ChangeOut, status_code=201)
async def propose_ai_change(project_id: int, body: AIChangeIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ChangeProposal:
    """Ask the model for a new version of ONE file chosen by the user. The model never chooses paths."""
    proj, ws = _ctx(project_id, db, user, state)
    clean, target = ws.resolve(body.path)
    original = ws.read_text(clean)[1] if target.is_file() else None
    provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                       body.provider, body.model)
    file_block = untrusted(f"file:{clean}", original) if original is not None else "(the file does not exist yet)"
    prompt = (f"Instruction from the user:\n{body.instruction}\n\nTarget file: {clean}\n"
              f"Current content:\n{file_block}\n\n"
              "Reply with a one-sentence summary of the change, then the COMPLETE new content of the file "
              "in a single fenced code block. Do not include anything after the code block.")
    system = build_system_prompt(db, user, proj)
    try:
        reply = await provider.complete([ChatMessage("user", prompt)], model=model, system=system)
    except ProviderFailure as exc:
        raise AppError(exc.message, code="provider_error", status_code=502) from exc
    match = _FENCE.search(reply)
    if not match:
        raise AppError("The model did not return file content in a code block. Try rephrasing.",
                       code="unparseable_model_output", status_code=502)
    content = match.group(1)
    summary = reply[: match.start()].strip()[:2000] or body.instruction[:500]
    cp = changes.propose(db, proj, ws, path=clean, content=content, summary=summary, source="ai")
    audit.record(db, "change.proposed", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id, "source": "ai", "provider": provider.name, "model": model})
    return cp


@router.get("/changes/{change_id}", response_model=ChangeOut)
def get_change(project_id: int, change_id: int, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> ChangeProposal:
    proj = owned_project(project_id, db, user)
    return changes.get_proposal(db, proj, change_id)


@router.post("/changes/{change_id}/apply", response_model=ChangeOut)
def apply_change(project_id: int, change_id: int, body: ApplyChangeIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ChangeProposal:
    proj, ws = _ctx(project_id, db, user, state)
    cp = changes.get_proposal(db, proj, change_id)
    try:
        changes.apply(db, ws, cp, body.proposed_sha256)
    except AppError as exc:
        audit.record(db, "change.applied", user_id=user.id, target=project_target(proj.id, cp.path),
                     outcome="denied", details={"change_id": cp.id, "reason": exc.code})
        raise
    audit.record(db, "change.applied", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id, "sha256": cp.proposed_sha256})
    return cp


@router.post("/changes/{change_id}/reject", response_model=ChangeOut)
def reject_change(project_id: int, change_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> ChangeProposal:
    proj = owned_project(project_id, db, user)
    cp = changes.reject(db, changes.get_proposal(db, proj, change_id))
    audit.record(db, "change.rejected", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id})
    return cp


@router.post("/changes/{change_id}/revert", response_model=ChangeOut)
def revert_change(project_id: int, change_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> ChangeProposal:
    proj, ws = _ctx(project_id, db, user, state)
    cp = changes.revert(db, ws, changes.get_proposal(db, proj, change_id))
    audit.record(db, "change.reverted", user_id=user.id, target=project_target(proj.id, cp.path),
                 details={"change_id": cp.id})
    return cp


# ---- AI project inspection (Category A: read-only) ------------------------------------------
_KEY_FILES = ("README.md", "readme.md", "package.json", "pyproject.toml", "requirements.txt", "setup.py",
              "Cargo.toml", "go.mod", "docker-compose.yml", "vite.config.ts", "manage.py")


@router.post("/assist/summary", response_model=ArtifactOut, status_code=201)
async def summarize_project(project_id: int, body: SummaryIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> Artifact:
    proj, ws = _ctx(project_id, db, user, state)
    tree = ws.tree()
    if not tree:
        raise AppError("The workspace is empty; there is nothing to summarise yet.", code="empty_workspace")
    excerpts: list[str] = []
    budget = 40_000
    for rel in [t for t in tree if t.rsplit("/", 1)[-1] in _KEY_FILES][:8]:
        try:
            _, text = ws.read_text(rel)
        except AppError:
            continue
        chunk = text[:8000]
        budget -= len(chunk)
        if budget < 0:
            break
        excerpts.append(untrusted(f"file:{rel}", chunk))
    provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                       body.provider, body.model)
    prompt = ("Inspect this project and write a Markdown summary with sections: Structure, Purpose, "
              "Key files explained, Potential problems, Suggested next steps. Only rely on the data below; "
              "say explicitly when something cannot be determined.\n"
              + (f"Focus requested by the user: {body.focus}\n" if body.focus else "")
              + "\nFile tree:\n" + untrusted("file-tree", "\n".join(tree)) + "\n\n" + "\n\n".join(excerpts))
    try:
        reply = await provider.complete([ChatMessage("user", prompt)], model=model,
                                        system=build_system_prompt(db, user, proj))
    except ProviderFailure as exc:
        raise AppError(exc.message, code="provider_error", status_code=502) from exc
    art = Artifact(project_id=proj.id, kind="project_summary", title=f"Project summary — {proj.name}",
                   content=reply, created_by="ai", source_input=body.focus)
    db.add(art)
    db.commit()
    audit.record(db, "artifact.generated", user_id=user.id, target=project_target(proj.id),
                 details={"artifact_id": art.id, "kind": art.kind, "provider": provider.name})
    return art
