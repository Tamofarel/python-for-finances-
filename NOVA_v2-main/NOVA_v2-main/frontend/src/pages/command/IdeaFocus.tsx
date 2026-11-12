// The idea in focus on the command center: one question at a time, then exploration, then the proposal.
// Approval of the proposal always happens on the workflow screen (never by voice).

import { Check, CircleDashed, Loader2, X } from "lucide-react";
import { Link } from "react-router-dom";
import type { WorkflowDetail } from "../../api/types";
import { Notice } from "../../components/ui";

export type FocusAction =
  | { type: "answer"; text: string }
  | { type: "skip" }
  | { type: "explore" }
  | { type: "retry" }
  | { type: "not_idea" }
  | { type: "leave" };

export function openQuestion(wf: WorkflowDetail) {
  return wf.questions.find((q) => q.status === "open") ?? null;
}

export function answeredCount(wf: WorkflowDetail): number {
  return wf.questions.filter((q) => q.status === "answered" && q.topic !== "intent").length;
}

/** What NOVA says out loud for this workflow state (the same words are on screen). */
export function focusSpeech(wf: WorkflowDetail): string | null {
  const q = openQuestion(wf);
  if (q) return q.question;
  if (wf.status === "awaiting_decision") {
    return "The proposal is ready. Review it on screen and tell me what to change. I create tasks only after you approve it.";
  }
  if (wf.status === "active" && !wf.running && wf.stages.some((s) => s.status === "failed")) {
    return "A stage failed. The details are on screen.";
  }
  if (wf.status === "active" && !wf.running && wf.next_stage) {
    return "I have enough to explore it. Shall I research it and draft the feasibility, architecture, user experience and MVP plan?";
  }
  return null;
}

const STAGE_ICON = { done: Check, skipped: Check, running: Loader2 } as const;

export function IdeaFocus({ wf, busy, error, onAction, stepByStep = false }: { wf: WorkflowDetail; busy: boolean; error: string | null; onAction: (a: FocusAction) => void; stepByStep?: boolean }) {
  const q = openQuestion(wf);
  const answered = answeredCount(wf);
  const note = [...wf.events].reverse().find((e) => e.kind === "note" || e.kind === "error");
  const failed = wf.stages.find((s) => s.status === "failed");
  const visibleStages = wf.stages.filter((s) => s.id !== "capture" && s.id !== "decision");

  return (
    <section className="focus" aria-label={`Idea: ${wf.title}`} aria-live="polite">
      <header className="focus-head">
        <div className="focus-name">
          <span className="focus-title">{wf.title}</span>
          <span className="small muted">{wf.template_label}</span>
        </div>
        <Link className="small" to={`/workflows/${wf.id}`}>Full workflow</Link>
        <button type="button" className="btn btn-ghost btn-icon" onClick={() => onAction({ type: "leave" })} aria-label="Put this idea aside"
          title="Put this idea aside (it stays in Ideas)">
          <X size={15} />
        </button>
      </header>

      {q ? (
        <div className="focus-body">
          <p className="focus-question">{q.question}</p>
          {q.rationale && <p className="small muted">{q.rationale}</p>}
          {q.options.length > 0 && (
            <div className="chips" role="group" aria-label="Suggested answers">
              {q.options.map((o) => (
                <button key={o} type="button" className="chip" disabled={busy} onClick={() => onAction({ type: "answer", text: o })}>{o}</button>
              ))}
            </div>
          )}
          <p className="small muted">Answer below or by voice. Say “skip” to skip a question{answered >= 2 ? " or “continue” to explore with what you have" : ""}.</p>
          <div className="row wrap">
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onAction({ type: "skip" })}>Skip question</button>
            {answered >= 2 && (
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onAction({ type: "explore" })}>Enough questions, explore it</button>
            )}
            {q.topic === "intent" && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onAction({ type: "not_idea" })}>
                Not an idea? Send it as a normal message
              </button>
            )}
          </div>
        </div>
      ) : wf.status === "awaiting_decision" ? (
        <div className="focus-body">
          <p className="focus-question">The proposal is ready.</p>
          <p className="muted">Review it and tell NOVA what to change. Tasks are created only after you approve it on the workflow screen.</p>
          <div className="row"><Link className="btn btn-primary btn-sm" to={`/workflows/${wf.id}`}>Review the proposal</Link></div>
        </div>
      ) : wf.status === "approved" ? (
        <div className="focus-body">
          <p className="focus-question">Approved — {wf.work_items.length} tasks created.</p>
          <div className="row"><Link className="btn btn-sm" to={`/projects/${wf.project_id}/work`}>Open the tasks</Link></div>
        </div>
      ) : (
        <div className="focus-body">
          {wf.running ? (
            <p className="focus-question">Working on {wf.stages.find((s) => s.status === "running")?.label.toLowerCase() ?? "the next stage"}…</p>
          ) : failed ? (
            <>
              <p className="focus-question">{failed.label} failed.</p>
              {failed.error && <Notice>{failed.error}</Notice>}
              <div className="row"><button type="button" className="btn btn-sm" disabled={busy} onClick={() => onAction({ type: "retry" })}>Try again</button></div>
            </>
          ) : wf.next_stage ? (
            <>
              <p className="focus-question">I have enough to explore it.</p>
              <p className="muted">NOVA will research it (only with a configured search provider), then draft feasibility, architecture options, the user experience and an MVP plan. Every draft is editable.</p>
              <div className="row wrap">
                <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => onAction({ type: "explore" })}>{stepByStep ? "Run next stage" : "Explore everything"}</button>
                <Link className="btn btn-ghost btn-sm" to={`/workflows/${wf.id}`}>Go step by step</Link>
              </div>
            </>
          ) : null}
          <ol className="focus-stages" aria-label="Progress">
            {visibleStages.map((s) => {
              const Icon = STAGE_ICON[s.status as keyof typeof STAGE_ICON] ?? CircleDashed;
              return (
                <li key={s.id} className={`fs fs-${s.status}`}>
                  <Icon size={13} aria-hidden className={s.status === "running" ? "spin" : ""} />
                  <span>{s.label}</span>
                  <span className="sr-only">: {s.status}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      {note && <p className="small muted focus-note">{note.message}</p>}
      {error && <Notice>{error}</Notice>}
    </section>
  );
}
