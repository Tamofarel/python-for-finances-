import { ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ActionRequest, Task } from "../api/types";
import { Modal, Notice, Spinner } from "./ui";

const CATEGORY_TEXT: Record<string, string> = {
  A: "Read-only",
  B: "Reversible change",
  C: "Consequential action — needs your explicit confirmation",
};

export function describeAction(a: ActionRequest): { what: string; affects: string } {
  if (a.kind === "run_command") {
    const argv = (a.params.argv as string[] | undefined) ?? [];
    return { what: `Run the command \`${argv.join(" ")}\``, affects: "The project workspace folder (working directory)" };
  }
  if (a.kind === "delete_path") {
    return { what: `Delete \`${String(a.params.path)}\``, affects: `File or empty folder ${String(a.params.path)} in the workspace` };
  }
  if (a.kind === "move_path") {
    return { what: `Move \`${String(a.params.src)}\` to \`${String(a.params.dst)}\``,
             affects: `${String(a.params.src)} and the destination ${String(a.params.dst)} in the workspace (nothing is overwritten)` };
  }
  if (a.kind === "tool_call") {
    const args = (a.params.arguments ?? {}) as Record<string, unknown>;
    const target = String(args.entity_id ?? "");
    const verb = String(args.action ?? "run").replace(/_/g, " ");
    const extra = args.temperature !== undefined ? ` to ${String(args.temperature)} °C` : "";
    return {
      what: target ? `${verb[0]!.toUpperCase()}${verb.slice(1)} ${target}${extra}` : `Run ${String(a.params.tool)}`,
      affects: target ? `The device ${target} (through Home Assistant). Its own manual controls keep working.` : String(a.params.tool),
    };
  }
  return { what: a.kind, affects: "Unknown" };
}

export function TaskResult({ task }: { task: Task }) {
  const finished = !["queued", "running"].includes(task.status);
  return (
    <div className="task-result">
      <div className="task-meta">
        <span className={`badge badge-${task.status}`}>{task.status.replace("_", " ")}</span>
        {task.exit_code !== null && <span>Exit code {task.exit_code}</span>}
        {task.duration_seconds !== null && <span>{task.duration_seconds.toFixed(2)} s</span>}
        {!finished && <Spinner label="Running" />}
      </div>
      {task.error && <Notice kind="warn">{task.error}</Notice>}
      {task.stdout && (
        <details open>
          <summary>Output</summary>
          <pre className="console">{task.stdout}</pre>
        </details>
      )}
      {task.stderr && (
        <details open={!task.stdout}>
          <summary>Errors and warnings</summary>
          <pre className="console console-err">{task.stderr}</pre>
        </details>
      )}
      {finished && !task.stdout && !task.stderr && <p className="muted small">The command produced no output.</p>}
    </div>
  );
}

/** Poll a task until it finishes. */
export function useTask(initial: Task | null) {
  const [task, setTask] = useState<Task | null>(initial);
  useEffect(() => setTask(initial), [initial]);
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    const t = setTimeout(async () => {
      try {
        setTask(await api.tasks.get(task.id));
      } catch {
        /* keep last known state */
      }
    }, 800);
    return () => clearTimeout(t);
  }, [task]);
  return task;
}

export function ApprovalDialog({ action, onClose, onDecided }: { action: ActionRequest; onClose: () => void; onDecided: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startedTask, setStartedTask] = useState<Task | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [decision, setDecision] = useState<"approved" | "rejected" | null>(action.status === "pending" ? null : "approved");
  const task = useTask(startedTask);
  const { what, affects } = describeAction(action);

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.actions.approve(action.id, action.params_hash);
      setDecision("approved");
      setStartedTask(res.task);
      setResult(res.result);
      onDecided();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    setBusy(true);
    try {
      await api.actions.reject(action.id);
      setDecision("rejected");
      onDecided();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={decision ? "Action result" : "Review this action"}
      onClose={onClose}
      wide
      footer={
        decision === null ? (
          <>
            <button type="button" className="btn" onClick={reject} disabled={busy}>Reject</button>
            <button type="button" className="btn btn-danger" onClick={approve} disabled={!confirmed || busy}>
              {busy ? "Working…" : "Approve and run"}
            </button>
          </>
        ) : (
          <button type="button" className="btn" onClick={onClose}>Close</button>
        )
      }
    >
      <dl className="approval">
        <dt>What NOVA will do</dt>
        <dd><code>{what.replace(/`/g, "")}</code></dd>
        <dt>Why</dt>
        <dd>{action.reason || <span className="muted">No reason given.</span>}</dd>
        <dt>Affects</dt>
        <dd>{affects}</dd>
        <dt>Reversible</dt>
        <dd>{action.reversible ? "Yes" : "No — this cannot be undone automatically"}</dd>
        <dt>Permission</dt>
        <dd><ShieldAlert size={14} aria-hidden /> Category {action.category}: {CATEGORY_TEXT[action.category]}</dd>
      </dl>
      {decision === null && (
        <label className="confirm-check">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>I reviewed this exact action and want NOVA to run it once.</span>
        </label>
      )}
      {error && <Notice>{error}</Notice>}
      {task && <TaskResult task={task} />}
      {result && typeof result.status === "string" ? (
        // A device action reports its own outcome: only "succeeded" is shown as done.
        <Notice kind={result.status === "succeeded" ? "ok" : "warn"}>{String(result.message ?? result.status)}</Notice>
      ) : result && <Notice kind="ok">Done: {JSON.stringify(result)}</Notice>}
    </Modal>
  );
}
