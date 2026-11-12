import { useState } from "react";
import { api } from "../../api/client";
import type { ActionRequest, Project, Task } from "../../api/types";
import { ApprovalDialog, TaskResult, describeAction } from "../../components/Approval";
import { Field, Notice, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";

const PRESETS = [
  { label: "Python tests", argv: "python -m pytest -q" },
  { label: "npm test", argv: "npm test" },
  { label: "npm run build", argv: "npm run build" },
  { label: "git status", argv: "git status" },
];

/** Split a command line into arguments (supports simple double quotes). No shell is ever involved. */
export function splitArgs(line: string): string[] {
  const out: string[] = [];
  const re = /"([^"]*)"|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line))) out.push(m[1] ?? m[2] ?? "");
  return out;
}

export function TasksTab({ project }: { project: Project }) {
  const [line, setLine] = useState("python -m pytest -q");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<ActionRequest | null>(null);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const tasks = useLoad(() => api.tasks.list(project.id), [project.id]);
  const actions = useLoad(() => api.actions.list("pending", project.id), [project.id]);

  async function request() {
    setError(null);
    try {
      setAction(await api.actions.requestCommand(project.id, splitArgs(line), reason));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function refresh() {
    await Promise.all([tasks.reload(), actions.reload()]);
  }

  return (
    <div className="two-col">
      <section className="stack">
        <h2 className="h3">Run a command</h2>
        <p className="small muted">Commands run in the workspace folder without a shell, with a timeout, and only if they start with an allowlisted prefix. Every run needs your approval.</p>
        <div className="row wrap">
          {PRESETS.map((p) => <button key={p.label} type="button" className="btn btn-xs" onClick={() => setLine(p.argv)}>{p.label}</button>)}
        </div>
        <Field label="Command"><input value={line} onChange={(e) => setLine(e.target.value)} spellCheck={false} className="mono-input" /></Field>
        <Field label="Why (shown in the approval)"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Check the fix before committing" /></Field>
        {error && <Notice>{error}</Notice>}
        <div><button type="button" className="btn btn-primary" disabled={!line.trim()} onClick={request}>Request approval</button></div>

        <h2 className="h3 top-gap">Waiting for approval</h2>
        {actions.data?.length ? (
          <ul className="plain-list">
            {actions.data.map((a) => (
              <li key={a.id}><button type="button" className="link-btn" onClick={() => setAction(a)}>{describeAction(a).what.replace(/`/g, "")}</button> <span className="small muted">{formatTime(a.created_at)}</span></li>
            ))}
          </ul>
        ) : <p className="muted small">Nothing is waiting.</p>}
      </section>
      <section className="stack">
        <div className="row"><h2 className="h3">Results</h2><span className="spacer" /><button type="button" className="btn btn-ghost btn-sm" onClick={refresh}>Refresh</button></div>
        {tasks.data?.length === 0 && <p className="muted small">No commands have run in this project.</p>}
        <ul className="plain-list selectable">
          {tasks.data?.map((t) => (
            <li key={t.id}>
              <button type="button" className={selectedTask?.id === t.id ? "active" : ""} onClick={() => setSelectedTask(t)}>
                <code>{(t.command ?? []).join(" ")}</code>
                <span className="small muted"><span className={`badge badge-${t.status}`}>{t.status.replace("_", " ")}</span> {formatTime(t.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
        {selectedTask && <TaskResult task={selectedTask} />}
      </section>
      {action && <ApprovalDialog action={action} onClose={() => { setAction(null); void refresh(); }} onDecided={refresh} />}
    </div>
  );
}
