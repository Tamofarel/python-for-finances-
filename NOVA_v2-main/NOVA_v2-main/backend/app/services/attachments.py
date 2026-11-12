"""Image attachments: validation by content (magic bytes), owner-scoped storage, and loading for providers."""

from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.models import MessageAttachment, User
from app.providers.base import ImageInput

EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
MAX_PER_MESSAGE = 4


def sniff_image(data: bytes) -> str | None:
    """Return the image MIME type from the file's content, or None. The client-supplied type is ignored."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120] or "image"


def user_dir(uploads: Path, user_id: int) -> Path:
    return uploads / str(int(user_id))


def save(db: Session, uploads: Path, user: User, conversation_id: int, filename: str, data: bytes,
         max_bytes: int) -> MessageAttachment:
    if not data:
        raise AppError("The file is empty.", code="empty_file")
    if len(data) > max_bytes:
        raise AppError(f"Images must be at most {max_bytes // 1_000_000} MB.", code="file_too_large", status_code=413)
    mime = sniff_image(data)
    if mime is None:
        raise AppError("Only PNG, JPEG, GIF and WebP images can be attached.", code="unsupported_type", status_code=415)
    folder = user_dir(uploads, user.id)
    folder.mkdir(parents=True, exist_ok=True)
    storage = f"{secrets.token_hex(16)}.{EXT[mime]}"
    (folder / storage).write_bytes(data)
    att = MessageAttachment(owner_id=user.id, conversation_id=conversation_id, filename=_safe_name(filename), mime=mime,
                            size=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_name=storage)
    db.add(att)
    db.commit()
    return att


def path_of(uploads: Path, att: MessageAttachment) -> Path:
    return user_dir(uploads, att.owner_id) / att.storage_name


def get_owned(db: Session, user: User, attachment_id: int) -> MessageAttachment:
    att = db.get(MessageAttachment, attachment_id)
    if att is None or att.owner_id != user.id:
        raise NotFound("Attachment not found.")
    return att


def claim(db: Session, user: User, conversation_id: int, ids: list[int]) -> list[MessageAttachment]:
    """Validate attachments for a new message: owned, same conversation, not yet used."""
    if len(ids) > MAX_PER_MESSAGE:
        raise AppError(f"At most {MAX_PER_MESSAGE} images per message.", code="too_many_attachments")
    out = []
    for i in dict.fromkeys(ids):
        att = get_owned(db, user, i)
        if att.conversation_id != conversation_id or att.message_id is not None:
            raise AppError("This image belongs to another message or conversation.", code="attachment_mismatch")
        out.append(att)
    return out


def load_images(uploads: Path, atts: list[MessageAttachment]) -> tuple[ImageInput, ...]:
    images = []
    for a in atts:
        p = path_of(uploads, a)
        if p.is_file():
            images.append(ImageInput(mime=a.mime, data=p.read_bytes()))
    return tuple(images)


def delete_for_conversation(db: Session, uploads: Path, conversation_id: int) -> int:
    n = 0
    for att in db.scalars(select(MessageAttachment).where(MessageAttachment.conversation_id == conversation_id)):
        path_of(uploads, att).unlink(missing_ok=True)
        n += 1
    return n
