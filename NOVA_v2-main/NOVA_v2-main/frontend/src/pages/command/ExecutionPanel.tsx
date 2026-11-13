// What NOVA is doing (or did) for the current request, from real backend events only:
// the task's steps and their states (SSE "task"), each tool call with its outcome and whether it was verified
// (SSE "action"), and what is waiting for the owner's approval. Execution and verification are shown separately.

import { CheckCircle2, CircleDashed, CircleSlash, Loader2, ShieldAlert, Workflow, XCircle } from "lucide-react";
import type { ActionRecord, Message, TaskState, TaskStepState } from "../../api/types";
import { ActionList, describeCall } from "../../components/ActionCard";
import { Panel } from "./panels";

const OUTCOME: Record<TaskState["status"], string> = {
  TASK_IN_PROGRESS: "In progress",
  TASK_COMPLETED: "Completed",
  TASK_PARTIALLY_COMPLETED: "Partly completed",
  TASK_FAILED: "Failed",
};

type StepView = TaskStepState | "RUNNING" | "WAITING";

const STEP_TEXT: Record<StepView, string> = {
  PENDING: "Pending",
  RUNNING: "Running",
  WAITING: "Waiting for you",
  ACTION_COMPLETED: "Done",
  ACTION_FAILED: "Failed",
  NOT_ATTEMPTED: "Not attempted",
};

function StepIcon({ state }: { state: StepView }) {
  const p = { size: 14, "aria-hidden": true } as const;
  if (state === "RUNNING") return <Loader2 {...p} className="spin" />;
  if (state === "WAITING") return <ShieldAlert {...p} />;
  if (state === "ACTION_COMPLETED") return <CheckCircle2 {...p} />;
  if (state === "ACTION_FAILED") return <XCircle {...p} />;
  if (state === "NOT_ATTEMPTED") return <CircleSlash {...p} />;
  return <CircleDashed {...p} />;
}

/** The actions still running in a reply (started, with no finished record yet). */
export function runningActions(actions: ActionRecord[] | null | undefined): ActionRecord[] {
  return (actions ?? []).filter((a) => a.phase === "started");
}

const WAITING_STATUSES = new Set(["awaiting_approval", "needs_input", "needs_authorization"]);

/** A step is shown as running while a started call to its tool has not finished yet, and as waiting for the owner
 *  when that call's real outcome is an approval, a choice or a connection (the task event reports those as not
 *  completed, which is true but not a failure). */
export function stepStates(task: TaskState, actions: ActionRecord[]): StepView[] {
  const running = new Set(runningActions(actions).map((a) => a.tool));
  const waiting = new Set(actions.filter((a) => a.phase === "finished" && WAITING_STATUSES.has(a.status ?? "")).map((a) => a.tool));
  let marked = false;
  return task.steps.map((s) => {
    if (s.state === "PENDING" && !marked && running.has(s.tool)) {
      marked = true;
      return "RUNNING";
    }
    if (s.state === "ACTION_FAILED" && waiting.has(s.tool)) return "WAITING";
    return s.state;
  });
}

export function hasExecution(m: Message | undefined): m is Message {
  return !!m && (!!m.task || !!m.actions?.length);
}

export function ExecutionPanel({ message, streaming }: { message: Message; streaming: boolean }) {
  const actions = message.actions ?? [];
  const task = message.task ?? null;
  const running = runningActions(actions);
  const waiting = actions.some((a) => a.status === "awaiting_approval");
  const outcome = waiting ? "Waiting for your approval" : running.length ? "Running" : task ? OUTCOME[task.status] : streaming ? "In progress" : "Finished";
  const states = task ? stepStates(task, actions) : [];
  return (
    <Panel title={`Current task · ${outcome}`} icon={waiting ? <ShieldAlert size={15} /> : <Workflow size={15} />}
      className={`execution-panel ${waiting ? "attention" : ""}`}>
      {running.length > 0 && <p className="small" role="status">Now: {describeCall(running[0]!)}</p>}
      {task && task.steps.length > 0 && (
        <ol className="exec-steps" aria-label="Steps">
          {task.steps.map((s, i) => (
            <li key={i} className={`exec-step exec-${states[i]!.toLowerCase()}`}>
              <StepIcon state={states[i]!} />
              <span className="exec-step-label">{s.step}</span>
              <span className="small muted">{STEP_TEXT[states[i]!]}</span>
            </li>
          ))}
        </ol>
      )}
      {/* Per call: the real outcome and, separately, whether it was verified. */}
      {actions.length > 0 && <ActionList actions={actions} reply={message.content} />}
    </Panel>
  );
}
