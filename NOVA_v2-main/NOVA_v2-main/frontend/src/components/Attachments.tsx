// Image attachments for a message: pick, paste or drop images; they are uploaded to the conversation and sent with
// the next message. The model only sees them if it supports images (the backend refuses otherwise and says so).

import { ImagePlus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Attachment } from "../api/types";
import { useChat } from "../store/chat";

export const ACCEPT = "image/png,image/jpeg,image/gif,image/webp";
export const MAX_IMAGES = 4;
const MAX_BYTES = 5_000_000;

interface Pending {
  key: string;
  name: string;
  preview: string;
  attachment: Attachment | null;
  error: string | null;
}

export function useAttachments() {
  const [items, setItems] = useState<Pending[]>([]);
  const current = useRef<Pending[]>([]);
  const commit = useCallback((next: Pending[]) => {
    current.current = next;
    setItems(next);
  }, []);
  const patch = useCallback((key: string, p: Partial<Pending>) =>
    commit(current.current.map((x) => (x.key === key ? { ...x, ...p } : x))), [commit]);
  const urls = useRef<string[]>([]);
  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const add = useCallback(async (files: File[]) => {
    const images = files.filter((f) => ACCEPT.split(",").includes(f.type));
    if (!images.length) return;
    const conv = await useChat.getState().ensureConversation();
    for (const file of images) {
      if (current.current.length >= MAX_IMAGES) break;
      const key = `${Date.now()}-${Math.random()}`;
      const preview = URL.createObjectURL(file);
      urls.current.push(preview);
      commit([...current.current, { key, name: file.name || "pasted image", preview, attachment: null, error: null }]);
      if (file.size > MAX_BYTES) {
        patch(key, { error: "Larger than 5 MB" });
        continue;
      }
      try {
        patch(key, { attachment: await api.conversations.attach(conv.id, file, file.name || "image.png") });
      } catch (e) {
        patch(key, { error: (e as Error).message });
      }
    }
  }, [commit, patch]);

  const remove = useCallback((key: string) => commit(current.current.filter((p) => p.key !== key)), [commit]);
  const clear = useCallback(() => commit([]), [commit]);
  const ids = items.filter((p) => p.attachment).map((p) => p.attachment!.id);
  const uploading = items.some((p) => !p.attachment && !p.error);
  /** Paste handler for text inputs: images on the clipboard are attached, text pastes normally. */
  const onPaste = useCallback((e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      void add(files);
    }
  }, [add]);
  return { items, add, remove, clear, ids, uploading, onPaste };
}

export function AttachButton({ onFiles, disabled }: { onFiles: (files: File[]) => void; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn btn-ghost btn-icon" disabled={disabled} onClick={() => ref.current?.click()}
        aria-label="Attach images" title="Attach images (PNG, JPEG, GIF or WebP, up to 5 MB each)">
        <ImagePlus size={17} aria-hidden />
      </button>
      <input ref={ref} type="file" accept={ACCEPT} multiple hidden onChange={(e) => {
        onFiles(Array.from(e.target.files ?? []));
        e.target.value = "";
      }} />
    </>
  );
}

export function PendingAttachments({ items, onRemove }: { items: Pending[]; onRemove: (key: string) => void }) {
  if (!items.length) return null;
  return (
    <ul className="attach-strip" aria-label="Images to send">
      {items.map((p) => (
        <li key={p.key} className={p.error ? "has-error" : ""}>
          <img src={p.preview} alt={p.name} />
          {!p.attachment && !p.error && <span className="spinner attach-spin" aria-label="Uploading" />}
          {p.error && <span className="attach-error small" role="alert">{p.error}</span>}
          <button type="button" className="attach-remove" onClick={() => onRemove(p.key)} aria-label={`Remove ${p.name}`}><X size={12} /></button>
        </li>
      ))}
    </ul>
  );
}

/** Images already sent with a message (served to the signed-in owner only). */
export function MessageImages({ items }: { items: Attachment[] }) {
  if (!items.length) return null;
  return (
    <ul className="msg-images">
      {items.map((a) => (
        <li key={a.id}>
          <a href={`/api/attachments/${a.id}`} target="_blank" rel="noreferrer">
            <img src={`/api/attachments/${a.id}`} alt={a.filename} loading="lazy" />
          </a>
        </li>
      ))}
    </ul>
  );
}
