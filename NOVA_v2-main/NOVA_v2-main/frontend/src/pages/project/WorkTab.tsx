import { Play, Plus, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { ActionRequest, Project, WorkEvent, WorkItem, WorkStatus } from "../../api/types";
import { ApprovalDialog } from "../../components/Approval";
import { Empty, Field, Modal, Notice, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { usePoll } from "../../hooks/usePoll";
import { splitArgs } from "./TasksTab";

const COLUMNS: { title: string; statuses: WorkStatus[] }[] = [
  { title: "To do", statuses: ["proposed", "todo", "blocked"] },
  { title: "In progress", statuses: ["in_progress", "awaiting_approval"] },
  { title: "Done", statuses: ["done"] },
  { title: "Stopped", statuses: ["failed", "cancelled"] },
];

const MANUAL_NEXT: Partial<Record<WorkStatus, { to: WorkStatus; label: string }[]>> = {
  todo: [{ to: "blocked", label: "Mark blocked" }, { to: "done", label: "Mark done" }],
  in_progress: [{ to: "done", label: "Mark done" }, { to: "blocked", label: "Mark blocked" }, { to: "failed", label: "Mark failed" }],
  blocked: [{ to: "todo", label: "Unblock" }],
  failed: [{ to: "todo", label: "Retry later" }],
  done: [{ to: "todo", label: "Reopen" }],
  cancelled: [{ to: "todo", label: "Reopen" }],
};

function ItemCard({ item, all, onChanged, onApprove }: { item: WorkItem; all: WorkItem[]; onChanged: () => void; onApprove: (a: ActionRequest) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<WorkEvent[] | null>(null);
  const deps = item.depends_on.map((d) => all.find((x) => x.id === d)).filter(Boolean) as WorkItem[];
  const waiting = deps.filter((d) => d.status !== "done");

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <article className={`work-card work-${item.status}`}>
      <header className="row">
        <span className={`badge badge-${item.status}`}>{item.status.replace("_", " ")}</span>
        <span className="small muted">#{item.id} {item.domain}</span>
        <span className="spacer" />
        {item.executor !== "manual" && <span className="small muted">{item.executor === "command" ? "command" : "document"}</span>}
      </header>
      <h3>{item.title}</h3>
      {item.description && <p className="small">{item.description}</p>}
      {deps.length > 0 && <p className="small muted">Depends on: {deps.map((d) => `#${d.id}`).join(", ")}{waiting.length ? ` (waiting for ${waiting.length})` : ""}</p>}
      {item.executor === "command" && <p className="small"><code>{(item.inputs.argv as string[] | undefined)?.join(" ")}</code></p>}
      {item.error && <p className="small danger-text">{item.error}</p>}
      {typeof item.outputs.artifact_id === "number" && (
        <p className="small"><Link to={`../design?artifact=${item.outputs.artifact_id}`}>Open the generated document</Link></p>
      )}
      {item.outputs.last_run !== undefined && (
        <p className="small muted">Last run: exit code {String((item.outputs.last_run as { exit_code: number | null }).exit_code)}</p>
      )}
      {error && <Notice>{error}</Notice>}
      <div className="row wrap">
        {["todo", "blocked", "failed"].includes(item.status) && (
          <button type="button" className="btn btn-xs" disabled={waiting.length > 0} title={waiting.length ? "Finish the dependencies first" : undefined}
            onClick={() => act(() => api.work.start(item.id))}>
            <Play size={12} aria-hidden /> {item.executor === "command" ? "Request approval to run" : item.executor === "artifact" ? "Generate" : "Start"}
          </button>
        )}
        {item.status === "awaiting_approval" && item.action_id && (
          <button type="button" className="btn btn-xs btn-primary" onClick={async () => {
            const a = (await api.actions.list("pending")).find((x) => x.id === item.action_id);
            if (a) onApprove(a);
            else onChanged();
          }}>Review approval</button>
        )}
        {item.executor === "manual" && MANUAL_NEXT[item.status]?.map((n) => (
          <button key={n.to} type="button" className="btn btn-ghost btn-xs" onClick={() => act(() => api.work.update(item.id, { status: n.to }))}>{n.label}</button>
        ))}
        {item.executor !== "manual" && ["failed", "cancelled", "done"].includes(item.status) && (
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => act(() => api.work.update(item.id, { status: "todo" }))}>Reset to to-do</button>
        )}
        {!["done", "cancelled"].includes(item.status) && (
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => act(() => api.work.cancel(item.id))}><X size={12} aria-hidden /> Cancel</button>
        )}
        <button type="button" className="btn btn-ghost btn-xs" onClick={async () => setEvents(events ? null : await api.work.events(item.id))}>
          {events ? "Hide history" : "History"}
        </button>
      </div>
      {events && (
        <ul className="events">
          {events.map((e) => <li key={e.id} className={`event-${e.kind}`}><span className="small muted">{formatTime(e.created_at)}</span> {e.message}</li>)}
        </ul>
      )}
    </article>
  );
}

export function WorkTab({ project }: { project: Project }) {
  const items = usePoll(() => api.work.list({ project_id: project.id }), 4000, [project.id]);
  const workflows = useLoad(() => api.workflows.list(project.id), [project.id]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", domain: "general", executor: "manual" as WorkItem["executor"], command: "", kind: "note", prompt: "", deps: [] as number[] });
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<ActionRequest | null>(null);
  const all = items.data ?? [];

  async function create() {
    setError(null);
    try {
      const inputs = form.executor === "command" ? { argv: splitArgs(form.command) } : form.executor === "artifact" ? { kind: form.kind, prompt: form.prompt } : {};
      await api.work.create(project.id, { title: form.title, description: form.description, domain: form.domain, executor: form.executor, inputs, depends_on: form.deps });
      setCreating(false);
      setForm({ ...form, title: "", description: "", command: "", prompt: "", deps: [] });
      await items.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="stack">
      <div className="row">
        <p className="small muted">Statuses change when you change them, or when NOVA observes a result: an approval, a command's exit code, or a generated document.</p>
        <span className="spacer" />
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)}><Plus size={14} aria-hidden /> New task</button>
      </div>
      {workflows.data && workflows.data.length > 0 && (
        <p className="small">Ideas in this project: {workflows.data.map((w) => <Link key={w.id} className="tag" to={`/workflows/${w.id}`}>{w.title}</Link>)}</p>
      )}
      {items.error && <Notice>{items.error}</Notice>}
      {all.length === 0 ? <Empty title="No tasks yet">Create one, or approve an idea's proposal to generate its implementation tasks.</Empty> : (
        <div className="board">
          {COLUMNS.map((col) => {
            const colItems = all.filter((i) => col.statuses.includes(i.status));
            return (
              <section key={col.title} className="board-col" aria-label={col.title}>
                <h3>{col.title} <span className="muted">{colItems.length}</span></h3>
                {colItems.map((i) => <ItemCard key={i.id} item={i} all={all} onChanged={() => void items.reload()} onApprove={setApproval} />)}
              </section>
            );
          })}
        </div>
      )}
      {creating && (
        <Modal title="New task" onClose={() => setCreating(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setCreating(false)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!form.title.trim()} onClick={create}>Create task</button>
          </>
        }>
          <Field label="Title"><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Description"><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Domain"><input value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })} placeholder="engineering, design, marketing…" /></Field>
          <Field label="How it is done" hint="Commands always need your approval before they run. Documents need a configured AI model.">
            <select value={form.executor} onChange={(e) => setForm({ ...form, executor: e.target.value as WorkItem["executor"] })}>
              <option value="manual">I do it and update the status</option>
              <option value="command">NOVA runs an allowlisted command</option>
              <option value="artifact">NOVA drafts a document</option>
            </select>
          </Field>
          {form.executor === "command" && (
            <Field label="Command"><input className="mono-input" value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} placeholder="python -m pytest -q" /></Field>
          )}
          {form.executor === "artifact" && (
            <>
              <Field label="Document type"><input value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} placeholder="requirements, risk_register, note…" /></Field>
              <Field label="What should it cover?"><textarea rows={3} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} /></Field>
            </>
          )}
          {all.length > 0 && (
            <Field label="Depends on">
              <select multiple value={form.deps.map(String)} onChange={(e) => setForm({ ...form, deps: Array.from(e.target.selectedOptions).map((o) => Number(o.value)) })}>
                {all.map((i) => <option key={i.id} value={i.id}>#{i.id} {i.title}</option>)}
              </select>
            </Field>
          )}
          {error && <Notice>{error}</Notice>}
        </Modal>
      )}
      {approval && <ApprovalDialog action={approval} onClose={() => { setApproval(null); void items.reload(); }} onDecided={() => void items.reload()} />}
    </div>
  );
}
