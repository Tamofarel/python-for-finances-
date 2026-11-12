// The command center: the core in the middle reflects NOVA's real state, the conversation is shown as captions,
// and context panels appear on the side only when there is something relevant (an approval, a running task, the
// idea in focus, a long reply). Text input is always here; voice is optional.

import { RotateCcw, Send } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { WorkflowDetail } from "../../api/types";
import { looksLikeIdea, voiceCommand } from "../../assistant/intent";
import { STATE_LABELS } from "../../assistant/machine";
import { AttachButton, PendingAttachments, useAttachments } from "../../components/Attachments";
import { Markdown } from "../../components/Markdown";
import { micLabel } from "../../components/VoiceStatus";
import { Notice } from "../../components/ui";
import { HoloCore } from "../../hologram/HoloCore";
import { Waveform } from "../../hologram/Waveform";
import { usePoll } from "../../hooks/usePoll";
import { dispatchAssistant, useAssistant } from "../../store/assistant";
import { useChat } from "../../store/chat";
import { usePrefs, useReducedMotion } from "../../store/prefs";
import { useProviders } from "../../store/providers";
import { useVoice } from "../../store/voice";
import { type FocusAction, IdeaFocus, focusSpeech, openQuestion } from "./IdeaFocus";
import { ApprovalsPanel, EXAMPLE_IDEA, ProjectPanel, ReplyPanel, SuggestionChips, TasksPanel, WorkflowDocsPanel, activeTasks } from "./panels";
import { InterruptButton, TalkButton } from "./VoiceControls";

const FOCUS_KEY = "nova.focusWorkflow";
const CAPTION_LIMIT = 420;

function readFocus(): number | null {
  try {
    const v = Number(sessionStorage.getItem(FOCUS_KEY));
    return Number.isInteger(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function writeFocus(id: number | null): void {
  try {
    if (id) sessionStorage.setItem(FOCUS_KEY, String(id));
    else sessionStorage.removeItem(FOCUS_KEY);
  } catch {
    /* storage unavailable: focus lasts for this view only */
  }
}

/** Replies with code, tables, diagrams or many paragraphs are read in the side panel rather than as a caption. */
function isLong(text: string): boolean {
  return text.length > CAPTION_LIMIT || /```|^\s*\|.+\|\s*$/m.test(text);
}

function caption(text: string): string {
  const plain = text.replace(/```[\s\S]*?(```|$)/g, " ").replace(/\s+/g, " ").trim();
  if (plain.length <= 220) return plain;
  const cut = plain.slice(0, 220);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
  return `${end > 80 ? cut.slice(0, end + 1) : cut}…`;
}

export function CommandCenter() {
  const state = useAssistant((s) => s.state);
  const detail = useAssistant((s) => s.detail);
  const errorText = useAssistant((s) => s.snapshot.error);
  const foreground = useAssistant((s) => s.snapshot.foreground);
  const chat = useChat();
  const voice = useVoice();
  const prefs = usePrefs((s) => s.prefs);
  const reducedMotion = useReducedMotion();
  const { data: providers } = useProviders();
  const [text, setText] = useState("");
  const [focusId, setFocusId] = useState<number | null>(readFocus);
  const [busy, setBusy] = useState(false);
  const [focusError, setFocusError] = useState<string | null>(null);
  const attachments = useAttachments();
  const lastVia = useRef<"text" | "voice">("text");
  const spokenFor = useRef<string>("");

  const suggestions = usePoll(() => (prefs.suggestions ? api.suggestions() : Promise.resolve([])), 10000, [prefs.suggestions]);
  const approvals = usePoll(() => api.actions.list("pending"), 5000);
  const work = usePoll(() => api.work.list(), 8000);
  const projects = usePoll(() => api.projects.list(), 30000);
  const focus = usePoll(() => (focusId ? api.workflows.get(focusId) : Promise.resolve(null)), 3000, [focusId]);
  const wf = focus.data && focus.data.id === focusId && focus.data.status !== "archived" ? focus.data : null;

  const setFocus = useCallback((id: number | null, detailNow?: WorkflowDetail) => {
    writeFocus(id);
    setFocusId(id);
    setFocusError(null);
    if (detailNow) focus.set(detailNow);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void voice.refreshPermission();
    void voice.loadCapabilities();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A focused workflow that was deleted or archived elsewhere leaves focus.
  useEffect(() => {
    if (focusId && focus.error) setFocus(null);
  }, [focusId, focus.error, setFocus]);

  // Speak the idea's question (or status) when the conversation about it is happening by voice.
  useEffect(() => {
    if (!wf) return;
    const line = focusSpeech(wf);
    const key = `${wf.id}:${openQuestion(wf)?.id ?? wf.status}:${wf.running}:${wf.next_stage}`;
    if (!line || key === spokenFor.current) return;
    spokenFor.current = key;
    const questionPending = !!openQuestion(wf);
    if (lastVia.current === "voice" && prefs.voice.speak_replies && (prefs.interaction_mode !== "quiet" || questionPending))
      voice.speak(line, { followUp: questionPending || !!wf.next_stage });
  }, [wf, prefs.voice.speak_replies, prefs.interaction_mode, voice]);

  // ---- requests -----------------------------------------------------------------------------------------
  /** Run a workflow request and show its real progress in the core (NOVA is waiting on the backend). */
  async function workflowRequest(fn: () => Promise<WorkflowDetail>): Promise<WorkflowDetail | null> {
    setBusy(true);
    setFocusError(null);
    dispatchAssistant({ type: "SEND" });
    try {
      const out = await fn();
      dispatchAssistant({ type: "STREAM_DONE" });
      // Reflect what the backend just reported without waiting for the next background poll.
      if (out.questions.some((q) => q.status === "open")) dispatchAssistant({ type: "CONTEXT", patch: { pendingQuestion: true } });
      if (out.running) dispatchAssistant({ type: "CONTEXT", patch: { job: out.current_stage === "research" ? "research" : "design" } });
      focus.set(out);
      return out;
    } catch (e) {
      const message = (e as Error).message;
      dispatchAssistant({ type: "STREAM_ERROR", message });
      setFocusError(message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function startIdea(idea: string) {
    const created = await workflowRequest(() => api.workflows.create({ idea, template: "auto" }));
    if (created) setFocus(created.id, created);
  }

  async function explore(current: WorkflowDetail) {
    await workflowRequest(async () => {
      let w = current;
      if (w.stages.find((s) => s.id === "clarify")?.status !== "done") w = await api.workflows.completeClarify(w.id);
      return prefs.interaction_mode === "step_by_step" ? api.workflows.continue(w.id) : api.workflows.continueAll(w.id);
    });
    void suggestions.reload();
  }

  async function onFocusAction(a: FocusAction) {
    if (!wf) return;
    const q = openQuestion(wf);
    switch (a.type) {
      case "answer":
        if (q) await workflowRequest(() => api.workflows.answer(wf.id, q.id, a.text));
        break;
      case "skip":
        if (q) await workflowRequest(() => api.workflows.skip(wf.id, q.id));
        break;
      case "explore":
      case "retry":
        await explore(wf);
        break;
      case "not_idea": {
        const idea = wf.idea;
        try {
          await api.workflows.dismiss(wf.id);
        } catch (e) {
          setFocusError((e as Error).message);
          return;
        }
        setFocus(null);
        await chat.send(idea);
        break;
      }
      case "leave":
        voice.stopSpeaking();
        setFocus(null);
        break;
    }
  }

  /** Every input — typed or spoken — goes through here. */
  const handleInput = useCallback(async (content: string, via: "text" | "voice") => {
    lastVia.current = via;
    const ids = attachments.ids;
    if (wf && !ids.length) {
      const q = openQuestion(wf);
      const cmd = voiceCommand(content);
      if (cmd === "stop") return onFocusAction({ type: "leave" });
      if (q) {
        if (cmd === "why") {
          if (prefs.voice.speak_replies) voice.speak(q.rationale || "This detail may change the recommendation. You can answer or skip it.");
          return;
        }
        if (cmd === "change") {
          if (prefs.voice.speak_replies) voice.speak("Tell me the approach you prefer, or give me a revised answer to this question.");
          return;
        }
        if (cmd === "skip") return onFocusAction({ type: "skip" });
        if (cmd === "continue" && wf.questions.some((x) => x.status === "answered" && x.topic !== "intent")) return onFocusAction({ type: "explore" });
        if (cmd === "yes" && q.options.length) return onFocusAction({ type: "answer", text: q.options[0]! });
        if (cmd === "assume") return onFocusAction({ type: "answer", text: "Assumption requested: choose a reasonable default for this decision; I may correct it later." });
        return onFocusAction({ type: "answer", text: content });
      }
      if (!wf.running && wf.status === "active" && wf.next_stage && (cmd === "continue" || cmd === "yes")) return onFocusAction({ type: "explore" });
    }
    if (!ids.length && looksLikeIdea(content)) return startIdea(content);
    attachments.clear();
    await chat.send(content, ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wf, attachments.ids, chat]);

  // Spoken transcripts come here while the command center is open.
  const handleRef = useRef(handleInput);
  handleRef.current = handleInput;
  useEffect(() => useVoice.getState().setTranscriptHandler((t) => void handleRef.current(t, "voice")), []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const content = text.trim();
    if (!content || chat.streaming || busy || attachments.uploading) return;
    setText("");
    await handleInput(content, "text");
  }

  function refreshAll() {
    void suggestions.reload();
    void approvals.reload();
    void focus.reload();
    void work.reload();
  }

  // ---- what to show -----------------------------------------------------------------------------------
  const messages = chat.current?.messages ?? [];
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const lastNova = [...messages].reverse().find((m) => m.role === "assistant");
  const longReply = !!lastNova?.content && isLong(lastNova.content);
  const sel = chat.override ?? providers?.selected ?? null;
  const prov = providers?.providers.find((p) => p.name === sel?.provider);
  const modelProblem = prov && (!prov.reachable || prov.error) ? prov.error ?? `${prov.label} is not reachable.` : null;
  const project = projects.data?.find((p) => p.id === (wf?.project_id ?? chat.current?.project_id)) ?? null;
  const tasks = activeTasks(work.data);
  const pending = approvals.data ?? [];
  const canContinue = lastNova?.status === "cancelled" && !chat.streaming;
  const levelSource = useMemo(() => (voice.listening || voice.mic === "armed" ? voice.level : null), [voice.listening, voice.mic, voice.level]);
  const samples = useMemo(() => (voice.listening ? voice.samples : null), [voice.listening, voice.samples]);
  const asking = wf ? openQuestion(wf) : null;

  const side = [
    pending.length > 0 && <ApprovalsPanel key="a" items={pending} onDecided={refreshAll} />,
    longReply && lastNova && chat.current && !wf && <ReplyPanel key="r" message={lastNova} conversationId={chat.current.id} />,
    wf && (wf.research.length > 0 || wf.stages.some((st) => st.artifacts.length > 0)) && <WorkflowDocsPanel key="w" workflow={wf} />,
    tasks.length > 0 && <TasksPanel key="t" items={tasks} />,
    project && !longReply && <ProjectPanel key="p" project={project} />,
  ].filter(Boolean).slice(0, 3);

  const idle = !wf && !lastUser && !voice.listening && !chat.streaming;

  return (
    <div className={`command ${side.length ? "has-side" : ""}`}>
      <section className={`stage ${wf || longReply ? "compact" : ""}`} aria-label="NOVA">
        <HoloCore state={state} levelSource={levelSource} pulse={voice.boundaryPulse} />
        <div className="core-status" role="status" aria-live="polite">
          <strong className={`state-name state-${state}`}>{STATE_LABELS[state]}</strong>
          {state === "error" && errorText && <span className="state-detail">{errorText}</span>}
          {(state === "researching" || state === "designing") && detail && <span className="state-detail">{detail}</span>}
          {state === "standby" && voice.mic !== "off" && <span className="state-detail">{micLabel(voice.mic, false, prefs.voice.wake_phrase)}</span>}
          {(state === "error" || state === "interrupted" || state === "cancelled") && (
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => dispatchAssistant({ type: "DISMISS" })}>Dismiss</button>
          )}
        </div>
        <Waveform active={voice.listening || voice.speaking} samples={samples} pulse={voice.boundaryPulse} speaking={voice.speaking} reducedMotion={reducedMotion} />

        <div className="captions" aria-live="polite">
          {voice.listening && <p className="caption-interim">{voice.interim || "Listening…"}</p>}
          {wf ? (
            <IdeaFocus wf={wf} busy={busy} error={focusError} stepByStep={prefs.interaction_mode === "step_by_step"}
              onAction={(a) => void onFocusAction(a)} />
          ) : (
            <>
              {!voice.listening && lastUser && <p className="caption-you"><span className="sr-only">You said: </span>{lastUser.content}</p>}
              {lastNova?.content && (
                longReply
                  ? <p className="caption-nova">{caption(lastNova.content)} <span className="small muted">The full reply is in the panel.</span></p>
                  : <div className="caption-nova"><Markdown>{lastNova.content}</Markdown></div>
              )}
              {lastNova?.status === "error" && <Notice>{lastNova.error ?? "The request failed."}</Notice>}
              {chat.streamError && !lastNova && <Notice>{chat.streamError.message}</Notice>}
              {focusError && <Notice onDismiss={() => setFocusError(null)}>{focusError}</Notice>}
            </>
          )}
          {idle && (
            <p className="hint">
              {voice.mic === "armed"
                ? <>Say “{prefs.voice.wake_phrase}”, hold <strong>Talk</strong>, or type. Describe an idea and NOVA will help you shape it.</>
                : <>Hold <strong>Talk</strong> or type. Describe an idea — for example, <button type="button" className="link-btn" onClick={() => setText(EXAMPLE_IDEA)}>a plant-health photo app</button> — and NOVA will help you shape it.</>}
            </p>
          )}
        </div>

        <div className="dock">
          <PendingAttachments items={attachments.items} onRemove={attachments.remove} />
          <form className="command-input" onSubmit={submit}>
            <AttachButton onFiles={(f) => void attachments.add(f)} disabled={!!wf} />
            <input value={text} onChange={(e) => setText(e.target.value)} onPaste={attachments.onPaste}
              placeholder={asking ? "Your answer" : "Message NOVA"} aria-label={asking ? "Your answer" : "Message NOVA"} />
            <TalkButton compact label="Talk" />
            <button type="submit" className="btn btn-primary btn-icon send-btn" aria-label="Send"
              disabled={!text.trim() || chat.streaming || busy || attachments.uploading}><Send size={16} aria-hidden /></button>
          </form>
          <div className="dock-row">
            <InterruptButton busy={chat.streaming} />
            {canContinue && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void chat.send("Please continue from where you stopped.")}>
                <RotateCcw size={13} aria-hidden /> Continue the reply
              </button>
            )}
            {!wf && !foreground && (
              <SuggestionChips items={suggestions.data ?? []} onFocusWorkflow={(id) => { lastVia.current = "text"; setFocus(id); }} />
            )}
          </div>
          {modelProblem && (
            <p className="small muted dock-note">
              <Link to="/providers">{prov?.label}</Link>: {modelProblem} Text and voice still work for everything that does not need a model.
            </p>
          )}
          {voice.error && <Notice kind="warn" onDismiss={voice.clearError}>{voice.error}</Notice>}
          {(voice.mic === "denied" || voice.mic === "error" || voice.mic === "suspended") && voice.micMessage && (
            <Notice kind="warn">
              {voice.micMessage}{" "}
              {voice.mic !== "suspended" && <button type="button" className="link-btn" onClick={() => void voice.retryMic()}>Try again</button>}
            </Notice>
          )}
        </div>
      </section>

      {side.length > 0 && <aside className="context" aria-label="Context">{side}</aside>}
    </div>
  );
}
