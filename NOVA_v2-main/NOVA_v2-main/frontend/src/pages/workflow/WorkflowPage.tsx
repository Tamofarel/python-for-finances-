import { Check, CircleDashed, Loader2, OctagonX, Pause, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { WorkflowDetail, WorkflowStage } from "../../api/types";
import { Markdown } from "../../components/Markdown";
import { Field, Modal, Notice, Spinner, formatTime } from "../../components/ui";
import { KIND_LABELS } from "../project/kinds";
import { TalkButton } from "../command/VoiceControls";

const STATUS_ICON: Record<string, JSX.Element> = {
  done: <Check size={14} aria-hidden />,
  running: <Loader2 size={14} className="spin" aria-hidden />,
  failed: <OctagonX size={14} aria-hidden />,
  interrupted: <Pause size={14} aria-hidden />,
  cancelled: <X size={14} aria-hidden />,
  skipped: <X size={14} aria-hidden />,
  pending: <CircleDashed size={14} aria-hidden />,
};

type Mode = "auto" | "ai" | "template";

function StageList({ wf, onRun, busy }: { wf: WorkflowDetail; onRun: (stage: string) => void; busy: boolean }) {
  const runnable = new Set(["research", "analysis", "architecture", "ux", "technology", "artifacts", "proposal"]);
  const clarified = wf.stages.find((s) => s.id === "clarify")?.status === "done";
  return (
    <ol className="stages">
      {wf.stages.map((s: WorkflowStage) => (
        <li key={s.id} className={`stage stage-${s.status} ${wf.current_stage === s.id ? "current" : ""}`}>
          <span className="stage-icon">{STATUS_ICON[s.status] ?? STATUS_ICON.pending}</span>
          <div className="stage-body">
            <div className="row">
              <strong>{s.label}</strong>
              <span className={`badge badge-${s.status}`}>{s.status}</span>
              {s.mode && <span className="small muted">{s.mode === "ai" ? "AI drafted" : "from your answers"}</span>}
              <span className="spacer" />
              {runnable.has(s.id) && clarified && wf.status !== "approved" && wf.status !== "rejected" && wf.status !== "archived" && (
                <button type="button" className="btn btn-ghost btn-xs" disabled={busy || wf.running} onClick={() => onRun(s.id)}>
                  {s.status === "done" ? "Run again" : "Run"}
                </button>
              )}
            </div>
            {s.error && <p className="small danger-text">{s.error}</p>}
            {s.note && <p className="small muted">{s.note}</p>}
            {s.artifacts.length > 0 && (
              <ul className="stage-docs">
                {s.artifacts.map((a) => (
                  <li key={a.id}>
                    <Link to={`/projects/${wf.project_id}/design?artifact=${a.id}`}>{KIND_LABELS[a.kind] ?? a.title}</Link>
                    <span className="small muted">{a.created_by === "ai" ? "AI draft" : "assembled from your answers"}{a.edited ? ", edited by you" : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function WorkflowPage() {
  const id = Number(useParams().id);
  const [wf, setWf] = useState<WorkflowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("auto");
  const [revising, setRevising] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [reopen, setReopen] = useState("proposal");

  async function load() {
    try {
      setWf(await api.workflows.get(id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Follow a running stage through its persisted state (no simulated progress).
  useEffect(() => {
    if (!wf?.running) return;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wf]);

  async function act(fn: () => Promise<WorkflowDetail>) {
    setBusy(true);
    setError(null);
    try {
      setWf(await fn());
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!wf) return <div className="page">{error ? <Notice>{error}</Notice> : <><Spinner /> Loading workflow…</>}</div>;
  const open = wf.questions.find((q) => q.status === "open");
  const clarifyDone = wf.stages.find((s) => s.id === "clarify")?.status === "done";
  const answered = wf.questions.filter((q) => q.status !== "open");
  const sourced = wf.research.filter((r) => r.status === "sourced").length;

  return (
    <div className="page workflow">
      <div className="page-head">
        <div>
          <h1>{wf.title}</h1>
          <p className="small muted">{wf.template_label}, <Link to={`/projects/${wf.project_id}`}>open project</Link>, started {formatTime(wf.created_at)}</p>
        </div>
        <span className={`badge badge-${wf.status}`}>{wf.status.replace("_", " ")}</span>
      </div>
      {error && <Notice onDismiss={() => setError(null)}>{error}</Notice>}

      <div className="workflow-grid">
        <div className="stack">
          <section className="card">
            <h2 className="h3">Idea</h2>
            <p className="pre-wrap">{wf.idea}</p>
          </section>

          {!clarifyDone && (
            <section className="card question-card big" aria-label="Current question">
              {open ? (
                <>
                  <p className="small muted">Question {wf.questions.length}, {open.source === "ai" ? "chosen by NOVA" : "from the template checklist"}</p>
                  <h2 className="question">{open.question}</h2>
                  {open.rationale && <p className="small muted">Why it matters: {open.rationale}</p>}
                  {open.options.length > 0 && (
                    <div className="row wrap">
                      {open.options.map((o) => <button key={o} type="button" className="btn btn-xs" onClick={() => setAnswer(o)}>{o}</button>)}
                    </div>
                  )}
                  <textarea rows={3} value={answer} onChange={(e) => setAnswer(e.target.value)} aria-label="Your answer" placeholder="Type or speak your answer" />
                  <div className="row">
                    <button type="button" className="btn btn-primary" disabled={!answer.trim() || busy}
                      onClick={() => act(async () => { const r = await api.workflows.answer(wf.id, open.id, answer.trim()); setAnswer(""); return r; })}>
                      {busy ? "Saving…" : "Answer"}
                    </button>
                    <TalkButton compact label="Answer by voice" target={(t) => setAnswer((a) => (a ? `${a} ${t}` : t))} />
                    <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act(() => api.workflows.skip(wf.id, open.id))}>Skip</button>
                    <span className="spacer" />
                    <button type="button" className="btn btn-sm" disabled={busy || answered.length === 0} onClick={() => act(() => api.workflows.completeClarify(wf.id))}>
                      Enough questions, continue
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <h2 className="h3">{wf.clarify_ready ? "The key decisions are covered" : "No question pending"}</h2>
                  <div className="row">
                    <button type="button" className="btn btn-primary" disabled={busy || answered.length === 0} onClick={() => act(() => api.workflows.completeClarify(wf.id))}>Continue to research</button>
                    <button type="button" className="btn btn-sm" disabled={busy} onClick={() => act(() => api.workflows.nextQuestion(wf.id))}>Ask me another question</button>
                  </div>
                </>
              )}
            </section>
          )}

          {clarifyDone && wf.status === "active" && (
            <section className="card">
              <div className="row wrap">
                {wf.running ? (
                  <>
                    <Spinner label="Stage running" /> <span>Running: {wf.stages.find((s) => s.status === "running")?.label}</span>
                    <span className="spacer" />
                    <button type="button" className="btn btn-sm" onClick={() => act(() => api.workflows.cancel(wf.id))}>Cancel</button>
                  </>
                ) : wf.next_stage ? (
                  <>
                    <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act(() => api.workflows.continue(wf.id, mode))}>
                      Run next: {wf.stages.find((s) => s.id === wf.next_stage)?.label}
                    </button>
                    <label className="small inline-field">
                      <span>Drafting</span>
                      <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
                        <option value="auto">AI when available</option>
                        <option value="ai">AI only (fail if unavailable)</option>
                        <option value="template">From my answers only</option>
                      </select>
                    </label>
                  </>
                ) : <span>All stages are done.</span>}
              </div>
            </section>
          )}

          {wf.status === "awaiting_decision" && wf.proposal && (
            <section className="card proposal" aria-label="Proposal">
              <h2 className="h3">Proposal ready for your decision</h2>
              <div className="proposal-body"><Markdown>{wf.proposal.content}</Markdown></div>
              <p className="small muted">Approving binds to this exact version of the proposal. If you edit it, review it again before approving.</p>
              <div className="row wrap">
                <button type="button" className="btn btn-primary" disabled={busy}
                  onClick={() => act(() => api.workflows.decide(wf.id, { decision: "approve", proposal_sha256: wf.proposal!.sha256 }))}>
                  Approve and create tasks
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => setRevising(true)}>Ask for a revision</button>
                <button type="button" className="btn btn-danger-outline" disabled={busy}
                  onClick={() => act(() => api.workflows.decide(wf.id, { decision: "reject" }))}>Reject</button>
                <Link className="small" to={`/projects/${wf.project_id}/design?artifact=${wf.proposal.artifact_id}`}>Edit proposal</Link>
              </div>
            </section>
          )}

          {(wf.status === "approved" || wf.work_items.length > 0) && (
            <section className="card">
              <h2 className="h3">Implementation tasks</h2>
              <ul className="plain-list">
                {wf.work_items.map((i) => (
                  <li key={i.id} className="task-line"><span className={`badge badge-${i.status}`}>{i.status.replace("_", " ")}</span> {i.title}</li>
                ))}
              </ul>
              <Link to={`/projects/${wf.project_id}/work`}>Open the task board</Link>
            </section>
          )}
          {wf.status === "rejected" && (
            <section className="card">
              <p>You rejected this proposal.</p>
              <button type="button" className="btn btn-sm" onClick={() => setRevising(true)}>Reopen with a revision</button>
            </section>
          )}

          <section className="card">
            <h2 className="h3">Decisions so far</h2>
            {answered.length === 0 ? <p className="muted small">No answers yet.</p> : (
              <dl className="qa">
                {answered.map((q) => (
                  <div key={q.id}>
                    <dt>{q.question}</dt>
                    <dd className={q.status === "skipped" ? "muted" : ""}>{q.status === "skipped" ? "Skipped" : q.answer}</dd>
                  </div>
                ))}
              </dl>
            )}
            {clarifyDone && wf.missing_decisions.length > 0 && (
              <Notice kind="warn">Missing decisions: {wf.missing_decisions.map((m) => m.question).join(" ")}</Notice>
            )}
          </section>
        </div>

        <div className="stack">
          <section className="card">
            <h2 className="h3">Stages</h2>
            <StageList wf={wf} busy={busy} onRun={(stage) => act(() => api.workflows.runStage(wf.id, stage, mode))} />
          </section>
          <section className="card">
            <h2 className="h3">Research</h2>
            {wf.research.length === 0 ? <p className="muted small">Research runs after clarification.</p> : (
              <>
                <p className="small muted">{sourced} of {wf.research.length} questions have real sources. Pending questions list where to look; nothing is filled in without a source.</p>
                <ul className="research-list">
                  {wf.research.map((r) => (
                    <li key={r.id}>
                      <div className="row"><span className={`badge badge-${r.status === "sourced" ? "done" : "pending"}`}>{r.status.replace("_", " ")}</span> <span>{r.question}</span></div>
                      {r.results.length > 0 ? (
                        <ul className="sources">
                          {r.results.map((s) => <li key={s.url}><a href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.title || s.url}</a></li>)}
                        </ul>
                      ) : <p className="small muted">Where to look: {r.source_categories.join("; ")}</p>}
                      {r.error && <p className="small danger-text">{r.error}</p>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <section className="card">
            <h2 className="h3">Activity</h2>
            <ul className="events">
              {wf.events.slice(0, 15).map((e) => (
                <li key={e.id} className={`event-${e.kind}`}><span className="small muted">{formatTime(e.created_at)}</span> {e.message}</li>
              ))}
            </ul>
          </section>
        </div>
      </div>

      {revising && (
        <Modal title="Ask for a revision" onClose={() => setRevising(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setRevising(false)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!feedback.trim()} onClick={async () => {
              setRevising(false);
              await act(() => api.workflows.decide(wf.id, { decision: "revise", feedback: feedback.trim(), reopen_stage: reopen }));
              setFeedback("");
            }}>Request revision</button>
          </>
        }>
          <Field label="What should change?"><textarea rows={4} value={feedback} onChange={(e) => setFeedback(e.target.value)} /></Field>
          <Field label="Restart from" hint="This stage and every later stage go back to pending. Your edited documents are never overwritten.">
            <select value={reopen} onChange={(e) => setReopen(e.target.value)}>
              {wf.stages.filter((s) => ["clarify", "research", "analysis", "architecture", "ux", "technology", "artifacts", "proposal"].includes(s.id))
                .map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </Field>
        </Modal>
      )}
    </div>
  );
}
