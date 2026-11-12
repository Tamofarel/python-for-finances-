// Conversation state lives in a global store so navigating to other screens never loses the
// current conversation or an in-flight generation.

import { create } from "zustand";
import { api, ApiError, streamPost } from "../api/client";
import type { ActionRecord, Conversation, ConversationDetail, Message } from "../api/types";

export type Presence = "idle" | "thinking" | "streaming" | "listening" | "speaking";

interface ChatState {
  conversations: Conversation[];
  listError: string | null;
  query: string;
  current: ConversationDetail | null;
  loadingCurrent: boolean;
  streaming: boolean;
  streamError: { message: string; retryable: boolean } | null;
  presence: Presence;
  override: { provider: string; model: string } | null;
  controller: AbortController | null;
  setQuery: (q: string) => void;
  setPresence: (p: Presence) => void;
  setOverride: (o: { provider: string; model: string } | null) => void;
  loadList: () => Promise<void>;
  open: (id: number) => Promise<void>;
  newConversation: (projectId?: number | null, mode?: string) => Promise<Conversation>;
  rename: (id: number, title: string) => Promise<void>;
  remove: (id: number) => Promise<void>;
  send: (content: string, attachmentIds?: number[]) => Promise<void>;
  ensureConversation: () => Promise<ConversationDetail>;
  retry: () => Promise<void>;
  cancel: () => void;
  clearCurrent: () => void;
}

// Stream events let other parts of the app (voice output, the assistant state machine) follow the one and only
// conversation pipeline instead of running a second engine.
export type ChatStreamEvent =
  | { type: "send" }
  | { type: "start" }
  | { type: "action"; action: ActionRecord } // a tool NOVA started or finished, with its real result
  | { type: "delta"; text: string; first: boolean }
  | { type: "error"; message: string }
  | { type: "aborted" }
  | { type: "end"; outcome: "done" | "error" | "aborted" };

const streamListeners = new Set<(e: ChatStreamEvent) => void>();
export function onChatStream(fn: (e: ChatStreamEvent) => void): () => void {
  streamListeners.add(fn);
  return () => streamListeners.delete(fn);
}
function emit(e: ChatStreamEvent): void {
  for (const fn of streamListeners) {
    try {
      fn(e);
    } catch {
      /* a listener must never break the conversation */
    }
  }
}

function patchLast(detail: ConversationDetail, fn: (m: Message) => Message): ConversationDetail {
  const msgs = detail.messages.slice();
  const last = msgs[msgs.length - 1];
  if (last) msgs[msgs.length - 1] = fn(last);
  return { ...detail, messages: msgs };
}

export const useChat = create<ChatState>((set, get) => {
  async function generate(path: string, body: unknown, optimisticUser: Message | null): Promise<void> {
    const cur = get().current;
    if (!cur) return;
    const controller = new AbortController();
    const placeholder: Message = {
      id: -Date.now(), role: "assistant", content: "", status: "streaming", error: null,
      provider: get().override?.provider ?? null, model: get().override?.model ?? null,
      created_at: new Date().toISOString(),
    };
    set({
      controller, streaming: true, streamError: null, presence: "thinking",
      current: { ...cur, messages: [...cur.messages, ...(optimisticUser ? [optimisticUser] : []), placeholder] },
    });
    emit({ type: "send" });
    let outcome: "done" | "error" | "aborted" = "done";
    let first = true;
    try {
      await streamPost(path, body, {
        onStart: (d) => {
          emit({ type: "start" });
          const c = get().current;
          if (!c) return;
          const msgs = c.messages.map((m) => {
            if (optimisticUser && m.id === optimisticUser.id && d.user_message) return d.user_message;
            if (m.id === placeholder.id) return { ...m, id: d.assistant_message_id, provider: d.provider, model: d.model };
            return m;
          });
          set({ current: { ...c, messages: msgs } });
        },
        onDelta: (text) => {
          const c = get().current;
          if (!c) return;
          set({ presence: "streaming", current: patchLast(c, (m) => ({ ...m, content: m.content + text })) });
          emit({ type: "delta", text, first });
          first = false;
        },
        onAction: (action) => {
          const c = get().current;
          if (c) {
            set({ current: patchLast(c, (m) => {
              const others = (m.actions ?? []).filter((a) => a.id !== action.id);
              return { ...m, actions: [...others, action] };
            }) });
          }
          emit({ type: "action", action });
        },
        onError: (d) => {
          const c = get().current;
          set({ streamError: { message: d.message, retryable: d.retryable } });
          if (c) set({ current: patchLast(c, (m) => ({ ...m, status: "error", error: d.message })) });
          outcome = "error";
          emit({ type: "error", message: d.message });
        },
        onDone: (msg) => {
          const c = get().current;
          if (c) set({ current: patchLast(c, () => msg) });
        },
      }, controller.signal);
      if (controller.signal.aborted) {
        outcome = "aborted";
        emit({ type: "aborted" });
        const c = get().current;
        if (c) set({ current: patchLast(c, (m) => (m.status === "streaming" ? { ...m, status: "cancelled", error: "Generation cancelled." } : m)) });
      }
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "The request failed.";
      const c = get().current;
      // Roll back the optimistic messages that were never accepted by the server.
      if (c) {
        set({ current: { ...c, messages: c.messages.filter((m) => m.id !== placeholder.id && m.id !== optimisticUser?.id) } });
      }
      set({ streamError: { message, retryable: false } });
      outcome = "error";
      emit({ type: "error", message });
    } finally {
      set({ streaming: false, controller: null, presence: "idle" });
      emit({ type: "end", outcome });
      await get().loadList();
      // The backend auto-titles a conversation from its first message; reflect that here.
      const c = get().current;
      const listed = c && get().conversations.find((x) => x.id === c.id);
      if (c && listed && listed.title !== c.title) set({ current: { ...c, title: listed.title } });
    }
  }

  return {
    conversations: [],
    listError: null,
    query: "",
    current: null,
    loadingCurrent: false,
    streaming: false,
    streamError: null,
    presence: "idle",
    override: null,
    controller: null,
    setQuery: (q) => set({ query: q }),
    setPresence: (p) => set({ presence: p }),
    setOverride: (o) => set({ override: o }),
    loadList: async () => {
      try {
        set({ conversations: await api.conversations.list(get().query || undefined), listError: null });
      } catch (e) {
        set({ listError: (e as Error).message });
      }
    },
    open: async (id) => {
      if (get().current?.id === id) return;
      get().controller?.abort();
      set({ loadingCurrent: true, streamError: null });
      try {
        set({ current: await api.conversations.get(id) });
      } finally {
        set({ loadingCurrent: false });
      }
    },
    newConversation: async (projectId, mode) => {
      const conv = await api.conversations.create(undefined, projectId ?? null, mode ?? "general");
      set({ current: { ...conv, messages: [] }, streamError: null });
      await get().loadList();
      return conv;
    },
    rename: async (id, title) => {
      const updated = await api.conversations.update(id, { title });
      const cur = get().current;
      if (cur?.id === id) set({ current: { ...cur, title: updated.title } });
      await get().loadList();
    },
    remove: async (id) => {
      await api.conversations.remove(id);
      if (get().current?.id === id) set({ current: null });
      await get().loadList();
    },
    ensureConversation: async () => {
      if (!get().current) await get().newConversation(null);
      return get().current!;
    },
    send: async (content, attachmentIds = []) => {
      let cur = get().current;
      if (!cur) {
        await get().newConversation(null);
        cur = get().current;
      }
      if (!cur || get().streaming) return;
      const o = get().override;
      const optimistic: Message = {
        id: -Date.now() - 1, role: "user", content, status: "complete", error: null, provider: null, model: null,
        created_at: new Date().toISOString(),
      };
      await generate(`/api/conversations/${cur.id}/messages`,
        { content, attachment_ids: attachmentIds, provider: o?.provider ?? null, model: o?.model ?? null }, optimistic);
    },
    retry: async () => {
      const cur = get().current;
      if (!cur || get().streaming) return;
      const last = cur.messages[cur.messages.length - 1];
      if (last?.role === "assistant") set({ current: { ...cur, messages: cur.messages.slice(0, -1) } });
      const o = get().override;
      await generate(`/api/conversations/${cur.id}/retry`, { provider: o?.provider ?? null, model: o?.model ?? null }, null);
    },
    cancel: () => get().controller?.abort(),
    clearCurrent: () => {
      get().controller?.abort();
      set({ current: null, streamError: null });
    },
  };
});
