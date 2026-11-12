from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, get_state, owned_project
from app.core.errors import AppError, NotFound
from app.core.logging import get_logger
from app.core.state import AppState
from app.models import Conversation, Message, MessageAttachment, Project, User, utcnow
from app.providers.base import AIProvider, ChatMessage, ProviderFailure
from app.schemas import (
    AttachmentOut,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    RetryIn,
    SendMessageIn,
)
from app.services import attachments as att_service
from app.services import audit
from app.services.agent import FOLLOW_UP_STATUSES, Agent, is_paused, no_action_rules, summarize_actions
from app.services.assistant import build_system_prompt, resolve_provider

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
log = get_logger("nova.chat")

HISTORY_MESSAGES = 40
HISTORY_CHARS = 60_000
IMAGE_HISTORY_MESSAGES = 6  # images are re-sent only for recent messages to bound request size


def _owned(db: Session, user: User, conv_id: int) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.owner_id != user.id:
        raise NotFound("Conversation not found.")
    return conv


@router.get("", response_model=list[ConversationOut])
def list_conversations(q: str | None = Query(default=None, max_length=200), project_id: int | None = None,
                       user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.owner_id == user.id)
    if project_id is not None:
        stmt = stmt.where(Conversation.project_id == project_id)
    if q:
        like = f"%{q}%"
        sub = select(Message.conversation_id).where(Message.content.ilike(like))
        stmt = stmt.where(or_(Conversation.title.ilike(like), Conversation.id.in_(sub)))
    return list(db.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(200)))


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> Conversation:
    if body.project_id is not None:
        owned_project(body.project_id, db, user)
    conv = Conversation(owner_id=user.id, title=body.title, project_id=body.project_id, mode=body.mode)
    db.add(conv)
    db.commit()
    return conv


@router.get("/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Conversation:
    return _owned(db, user, conv_id)


@router.patch("/{conv_id}", response_model=ConversationOut)
def update_conversation(conv_id: int, body: ConversationUpdate, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> Conversation:
    conv = _owned(db, user, conv_id)
    if body.title is not None:
        conv.title = body.title
    if body.mode is not None:
        conv.mode = body.mode
    if body.detach_project:
        conv.project_id = None
    elif body.project_id is not None:
        owned_project(body.project_id, db, user)
        conv.project_id = body.project_id
    db.commit()
    return conv


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                        state: AppState = Depends(get_state)) -> Response:
    conv = _owned(db, user, conv_id)
    title = conv.title
    removed = att_service.delete_for_conversation(db, state.settings.uploads_dir, conv.id)
    db.delete(conv)
    db.commit()
    audit.record(db, "conversation.deleted", user_id=user.id, target=title, details={"images_deleted": removed})
    return Response(status_code=204)


def _history(conv: Conversation, uploads: Path) -> list[ChatMessage]:
    msgs = [m for m in conv.messages if m.status == "complete" and (m.content.strip() or m.actions)]
    out: list[ChatMessage] = []
    total = 0
    for i, m in enumerate(reversed(msgs[-HISTORY_MESSAGES:])):
        total += len(m.content)
        if total > HISTORY_CHARS and out:
            break
        images = att_service.load_images(uploads, m.attachments) if m.attachments and i < IMAGE_HISTORY_MESSAGES else ()
        content = m.content
        if m.attachments and not images:
            content = f"{content}\n[{len(m.attachments)} earlier image(s) not re-sent]"
        if m.actions:  # what NOVA actually did, so follow-ups ("the first one") have the real results
            content = f"{content}\n\n{summarize_actions(m.actions)}".strip()
        out.append(ChatMessage(role="assistant" if m.role == "assistant" else "user", content=content, images=images))
    out.reverse()
    # Providers require the first message to be from the user.
    while out and out[0].role != "user":
        out.pop(0)
    return out


def _request_context(conv: Conversation) -> tuple[str, str | None, dict | None]:
    """The owner's current message; the previous one when NOVA's last reply was still waiting on the owner (a choice,
    a connection, a retry), so that "the first one" continues the same request; and the choice NOVA offered, if any."""
    msgs = [m for m in conv.messages if m.status == "complete"]
    users = [m for m in msgs if m.role == "user"]
    current = users[-1].content if users else ""
    previous_reply = next((m for m in reversed(msgs) if m.role == "assistant"), None)
    finished = [a for a in (previous_reply.actions or [] if previous_reply else []) if a.get("phase") == "finished"]
    waiting = any(a.get("status") in FOLLOW_UP_STATUSES for a in finished)
    choice = next((a for a in finished if a.get("status") == "needs_input" and (a.get("data") or {}).get("candidates")),
                  None)
    return current, (users[-2].content if waiting and len(users) >= 2 else None), choice


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _stream_generation(state: AppState, user: User, conv_id: int, provider: AIProvider, model: str,
                       request: Request, user_message: Message | None) -> StreamingResponse:
    """Create the assistant message, then stream real provider output while persisting it."""
    with state.db.SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        assert conv is not None
        project = db.get(Project, conv.project_id) if conv.project_id else None
        system = build_system_prompt(db, user, project, mode=conv.mode)
        history = _history(conv, state.settings.uploads_dir)
        request_text, follow_up_of, pending_choice = _request_context(conv)
        paused = is_paused(db, user)
        project_id = project.id if project else None
        workspace_root = Path(project.workspace_path) if project else None
        assistant = Message(conversation_id=conv.id, role="assistant", content="", status="streaming",
                            provider=provider.name, model=model)
        db.add(assistant)
        conv.provider, conv.model, conv.updated_at = provider.name, model, utcnow()
        db.commit()
        assistant_id = assistant.id
        start_payload = {"assistant_message_id": assistant_id, "provider": provider.name, "model": model,
                         "user_message": MessageOut.model_validate(user_message).model_dump(mode="json")
                         if user_message else None}
    agent = Agent(state, provider=provider, model=model, owner_id=user.id, conversation_id=conv_id,
                  project_id=project_id, workspace_root=workspace_root, paused=paused,
                  is_disconnected=request.is_disconnected, request_text=request_text, follow_up_of=follow_up_of,
                  pending_choice=pending_choice)

    async def plain(prompt: str) -> AsyncIterator[tuple[str, dict]]:
        async for chunk in provider.stream(history, model=model, system=prompt):
            yield "delta", {"text": chunk}

    async def events() -> AsyncIterator[tuple[str, dict]]:
        """The model with the enabled tools offered, or plain streaming when no tool can be offered."""
        direct = agent.deterministic_call()
        if direct is not None:
            async for ev in agent.run_direct(direct):
                yield ev
            return
        tools, rules = await agent.prepare()
        prompt = f"{system}\n\n{rules}"
        if not tools:
            async for ev in plain(prompt):
                yield ev
            return
        produced = False
        try:
            async for ev in agent.run(history, prompt, tools):
                produced = True
                yield ev
        except ProviderFailure as exc:
            if exc.kind != "tools_unsupported" or produced:
                raise
            # The model refused the tools: answer without them, and let it say why it cannot act.
            async for ev in plain(f"{system}\n\n" + no_action_rules(
                    f"the selected model '{model}' cannot call tools. A model with tool support (for example "
                    "llama3.2 or mistral in Ollama, or a Claude model) can.")):
                yield ev

    async def gen() -> AsyncIterator[str]:
        parts: list[str] = []
        final_status, error = "cancelled", None
        generation_started = time.monotonic()
        first_response_at: float | None = None
        try:
            yield _sse("start", start_payload)
            if not history:
                raise ProviderFailure("There is no user message to answer.", kind="empty")
            async for kind, data in events():
                if kind == "delta":
                    if first_response_at is None:
                        first_response_at = time.monotonic()
                    parts.append(data["text"])
                yield _sse(kind, data)
                if await request.is_disconnected():
                    break
            else:
                final_status = "complete"
            if final_status == "complete" and not "".join(parts).strip() and not agent.actions:
                final_status, error = "error", "The provider returned an empty response."
        except ProviderFailure as exc:
            final_status, error = "error", exc.message
            yield _sse("error", {"message": exc.message, "kind": exc.kind, "retryable": exc.retryable})
        finally:
            with state.db.SessionLocal() as db:
                msg = db.get(Message, assistant_id)
                if msg is not None:
                    msg.content = "".join(parts)
                    msg.actions = agent.actions or None
                    msg.status = final_status
                    msg.error = error if final_status == "error" else (
                        "Generation cancelled." if final_status == "cancelled" else None)
                    db.commit()
                    done = MessageOut.model_validate(msg).model_dump(mode="json")
                else:
                    done = None
            if final_status == "error":
                log.info("generation failed: %s", error)
            log.info("response generation finished", extra={"extra_data": {
                "provider": provider.name, "model": model, "status": final_status,
                "time_to_first_response_ms": round((first_response_at - generation_started) * 1000)
                if first_response_at is not None else None,
                "duration_ms": round((time.monotonic() - generation_started) * 1000)}})
        if done is not None and final_status != "cancelled":
            yield _sse("done", {"message": done})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{conv_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(conv_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                            db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> MessageAttachment:
    conv = _owned(db, user, conv_id)
    data = await file.read(state.settings.max_image_bytes + 1)
    att = att_service.save(db, state.settings.uploads_dir, user, conv.id, file.filename or "image", data,
                           state.settings.max_image_bytes)
    audit.record(db, "attachment.uploaded", user_id=user.id, target=f"conversation:{conv.id}",
                 details={"attachment_id": att.id, "mime": att.mime, "size": att.size})
    return att


@router.post("/{conv_id}/messages")
async def send_message(conv_id: int, body: SendMessageIn, request: Request, user: User = Depends(current_user),
                       db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> StreamingResponse:
    conv = _owned(db, user, conv_id)
    atts = att_service.claim(db, user, conv.id, body.attachment_ids) if body.attachment_ids else []
    if any(m.status == "streaming" for m in conv.messages):
        # Clean up any generation that was interrupted by a backend restart.
        for m in conv.messages:
            if m.status == "streaming":
                m.status, m.error = "error", "Interrupted."
    prov_name = body.provider or conv.provider
    model_name = body.model or (conv.model if prov_name == conv.provider else None)
    provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                       prov_name, model_name)
    if atts and await provider.supports_images(model) is False:
        raise AppError(f"The selected model '{model}' cannot read images. Choose a vision model in Models "
                       "(for example llama3.2-vision in Ollama, or a Claude model).", code="vision_unsupported")
    msg = Message(conversation_id=conv.id, role="user", content=body.content)
    db.add(msg)
    db.flush()
    for a in atts:
        a.message_id = msg.id
    if conv.title == "New conversation":
        conv.title = body.content.strip().splitlines()[0][:60] or conv.title
    conv.updated_at = utcnow()
    db.commit()
    return _stream_generation(state, user, conv.id, provider, model, request, msg)


@router.post("/{conv_id}/retry")
def retry(conv_id: int, body: RetryIn, request: Request, user: User = Depends(current_user),
          db: Session = Depends(get_db), state: AppState = Depends(get_state)) -> StreamingResponse:
    conv = _owned(db, user, conv_id)
    if not conv.messages:
        raise AppError("Nothing to retry.", code="nothing_to_retry")
    last = conv.messages[-1]
    if last.role == "assistant":
        db.delete(last)
        db.commit()
        db.refresh(conv)
    if not conv.messages or conv.messages[-1].role != "user":
        raise AppError("The last message is not a user message.", code="nothing_to_retry")
    provider, model = resolve_provider(db, user, state.providers, state.settings.default_provider,
                                       body.provider or conv.provider, body.model or conv.model)
    return _stream_generation(state, user, conv.id, provider, model, request, None)


attachments_router = APIRouter(prefix="/api/attachments", tags=["conversations"])


@attachments_router.get("/{attachment_id}")
def get_attachment(attachment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                   state: AppState = Depends(get_state)) -> FileResponse:
    att = att_service.get_owned(db, user, attachment_id)
    path = att_service.path_of(state.settings.uploads_dir, att)
    if not path.is_file():
        raise NotFound("The image file is missing.")
    return FileResponse(path, media_type=att.mime, headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{att.filename}"'})
