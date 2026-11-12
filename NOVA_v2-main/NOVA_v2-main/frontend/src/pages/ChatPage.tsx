import { Mic, MicOff, Plus, RotateCcw, Search, Send, Square, Volume2, VolumeX } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ConversationMode, Message, Project } from "../api/types";
import { ActionList } from "../components/ActionCard";
import { AttachButton, MessageImages, PendingAttachments, useAttachments } from "../components/Attachments";
import { CopyButton, Markdown } from "../components/Markdown";
import { Orb } from "../components/Orb";
import { Notice, Spinner, formatTime } from "../components/ui";
import { usePrefs } from "../store/prefs";
import { useVoice } from "../store/voice";
import { useChat } from "../store/chat";

const STARTERS = [
  "Help me evaluate an app idea: list the assumptions I should test first.",
  "Explain the trade-offs between SSE and WebSockets for streaming AI responses.",
  "Review this plan and tell me what could go wrong:",
];

function MessageView({ m, isLast, onRetry, onSpeak, canSpeak }: { m: Message; isLast: boolean; onRetry: () => void; onSpeak: (t: string) => void; canSpeak: boolean }) {
  const mine = m.role === "user";
  return (
    <article className={`msg ${mine ? "msg-user" : "msg-nova"} msg-${m.status}`} aria-label={mine ? "Your message" : "NOVA's reply"}>
      <header className="msg-head">
        <strong>{mine ? "You" : "NOVA"}</strong>
        <time dateTime={m.created_at}>{formatTime(m.created_at)}</time>
        {!mine && m.model && <span className="muted small">{m.model}</span>}
      </header>
      {m.attachments && m.attachments.length > 0 && <MessageImages items={m.attachments} />}
      {!mine && m.actions && m.actions.length > 0 && <ActionList actions={m.actions} reply={m.content} />}
      {m.content ? (
        mine ? <p className="msg-text">{m.content}</p> : <Markdown>{m.content}</Markdown>
      ) : m.status === "streaming" && !m.actions?.length ? (
        <p className="muted"><Spinner label="Waiting for the model" /> Waiting for the model…</p>
      ) : null}
      {m.status === "error" && <Notice>{m.error ?? "The request failed."}</Notice>}
      {m.status === "cancelled" && <p className="muted small">Generation stopped{m.content ? " — partial answer kept." : "."}</p>}
      {!mine && m.status !== "streaming" && (
        <div className="msg-actions">
          {m.content && <CopyButton text={m.content} />}
          {m.content && canSpeak && (
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => onSpeak(m.content)}>
              <Volume2 size={14} /> <span>Read aloud</span>
            </button>
          )}
          {isLast && (
            <button type="button" className="btn btn-ghost btn-xs" onClick={onRetry}>
              <RotateCcw size={14} /> <span>Retry</span>
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export function ConversationList({ compact = false }: { compact?: boolean }) {
  const { conversations, listError, query, setQuery, loadList, current } = useChat();
  const navigate = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => void loadList(), 200);
    return () => clearTimeout(t);
  }, [query, loadList]);
  return (
    <div className={`conv-list ${compact ? "compact" : ""}`}>
      <div className="search-box">
        <Search size={15} aria-hidden />
        <input type="search" placeholder="Search conversations" aria-label="Search conversations" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {listError && <Notice>{listError}</Notice>}
      {conversations.length === 0 && !listError ? (
        <p className="muted small pad">{query ? "No conversation matches that search." : "Your conversations will appear here."}</p>
      ) : (
        <ul>
          {conversations.map((c) => (
            <li key={c.id}>
              <button type="button" className={current?.id === c.id ? "active" : ""} onClick={() => navigate(`/c/${c.id}`)}>
                <span className="conv-title">{c.title}</span>
                <span className="small muted">{formatTime(c.updated_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const chat = useChat();
  const { current, streaming, streamError, loadingCurrent } = chat;
  const [draft, setDraft] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [openError, setOpenError] = useState<string | null>(null);
  const [modes, setModes] = useState<ConversationMode[]>([]);
  const voice = useVoice();
  const engine = usePrefs((s) => s.prefs.voice.stt_engine);
  const speakReplies = usePrefs((s) => s.prefs.voice.speak_replies);
  const setVoicePrefs = usePrefs((s) => s.setVoice);
  const attachments = useAttachments();
  const canListen = engine !== "browser" || voice.input.supported;
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => setProjects([]));
    api.modes().then(setModes).catch(() => setModes([]));
  }, []);

  useEffect(() => {
    if (!id) return;
    setOpenError(null);
    chat.open(Number(id)).catch((e: Error) => setOpenError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (!id && current) navigate(`/c/${current.id}`, { replace: true });
  }, [id, current, navigate]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [current?.messages.length, current?.messages[current.messages.length - 1]?.content.length]);

  async function submit(text = draft) {
    const content = text.trim();
    if (!content || streaming || attachments.uploading) return;
    setDraft("");
    const ids = attachments.ids;
    attachments.clear();
    await chat.send(content, ids);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  async function startNew() {
    chat.clearCurrent();
    navigate("/chat");
    inputRef.current?.focus();
  }

  async function setMode(mode: string) {
    if (!current) return;
    await api.conversations.update(current.id, { mode });
    useChat.setState({ current: { ...current, mode } });
  }

  async function attachProject(value: string) {
    if (!current) return;
    await api.conversations.update(current.id, value ? { project_id: Number(value) } : { detach_project: true });
    useChat.setState({ current: await api.conversations.get(current.id) });
  }

  const messages = current?.messages ?? [];
  const project = projects.find((p) => p.id === current?.project_id);

  return (
    <div className="chat-layout">
      <aside className="chat-side" aria-label="Conversations">
        <button type="button" className="btn btn-primary btn-block" onClick={startNew}>
          <Plus size={16} aria-hidden /> New conversation
        </button>
        <ConversationList compact />
      </aside>
      <section className="chat-main">
        {current && (
          <div className="chat-head">
            <h1 className="chat-title">{current.title}</h1>
            <label className="small inline-field">
              <span>Project</span>
              <select value={current.project_id ?? ""} onChange={(e) => void attachProject(e.target.value)} disabled={streaming}>
                <option value="">None</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            {project && <Link className="small" to={`/projects/${project.id}`}>Open project</Link>}
            {modes.length > 0 && (
              <label className="small inline-field">
                <span>Focus</span>
                <select value={current.mode ?? "general"} onChange={(e) => void setMode(e.target.value)} disabled={streaming}
                  title={modes.find((m) => m.id === (current.mode ?? "general"))?.prompt}>
                  {modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </label>
            )}
          </div>
        )}
        <div className="thread" aria-live="polite" aria-busy={streaming}>
          {openError && <Notice>{openError}</Notice>}
          {loadingCurrent && <p className="muted"><Spinner /> Loading conversation…</p>}
          {!loadingCurrent && messages.length === 0 && (
            <div className="welcome">
              <Orb presence={chat.streaming ? "thinking" : voice.listening ? "listening" : voice.speaking ? "speaking" : "idle"} size={96} />
              <h2>What are we working on?</h2>
              <p className="muted">Ask a question, think through an idea, or attach a project to work on its files.</p>
              <div className="starters">
                {STARTERS.map((s) => (
                  <button key={s} type="button" className="starter" onClick={() => { setDraft(s); inputRef.current?.focus(); }}>{s}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageView key={m.id} m={m} isLast={i === messages.length - 1} onRetry={() => void chat.retry()} onSpeak={voice.speak} canSpeak={voice.output.supported} />
          ))}
          <div ref={endRef} />
        </div>
        {streamError && !messages.some((m) => m.status === "error") && (
          <Notice onDismiss={() => useChat.setState({ streamError: null })}>
            {streamError.message} {streamError.message.includes("not configured") && <Link to="/providers">Open model settings</Link>}
          </Notice>
        )}
        {voice.error && <Notice kind="warn">{voice.error}</Notice>}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <PendingAttachments items={attachments.items} onRemove={attachments.remove} />
          <textarea
            onPaste={attachments.onPaste}
            ref={inputRef}
            value={voice.listening ? voice.interim || draft : draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            placeholder={voice.listening ? "Listening…" : "Message NOVA  (Enter to send, Shift+Enter for a new line)"}
            aria-label="Message NOVA"
            rows={Math.min(8, Math.max(2, draft.split("\n").length))}
          />
          <div className="composer-bar">
            <AttachButton onFiles={(f) => void attachments.add(f)} disabled={streaming} />
            {canListen ? (
              <button
                type="button"
                className={`btn btn-ghost btn-sm ${voice.listening ? "is-on" : ""}`}
                onClick={() => (voice.listening ? voice.stopListening() : void voice.startListening((t) => setDraft((d) => (d ? `${d} ${t}` : t))))}
                aria-pressed={voice.listening}
                title={engine === "browser" ? voice.input.privacyNote : "Speech is transcribed by NOVA on this computer."}
              >
                {voice.listening ? <MicOff size={15} /> : <Mic size={15} />} {voice.listening ? "Stop listening" : "Speak"}
              </button>
            ) : (
              <span className="small muted">No speech recognition in this browser — choose a local engine in Settings → Voice.</span>
            )}
            {voice.output.supported && (
              voice.speaking ? (
                <button type="button" className="btn btn-ghost btn-sm" onClick={voice.stopSpeaking}><VolumeX size={15} /> Stop speaking</button>
              ) : (
                <label className="small toggle">
                  <input type="checkbox" checked={speakReplies} onChange={(e) => void setVoicePrefs({ speak_replies: e.target.checked }).catch(() => undefined)} /> Answer voice requests aloud
                </label>
              )
            )}
            <span className="spacer" />
            {streaming ? (
              <button type="button" className="btn btn-sm" onClick={chat.cancel}><Square size={14} /> Stop</button>
            ) : (
              <button type="submit" className="btn btn-primary btn-sm" disabled={!draft.trim() || attachments.uploading}><Send size={14} /> Send</button>
            )}
          </div>
          {voice.listening && <p className="small muted">{voice.listenVia === "local" ? "Speech is transcribed by NOVA on this computer." : "Speech is transcribed by your browser."} Audio is only captured while this button is active.</p>}
        </form>
      </section>
    </div>
  );
}
