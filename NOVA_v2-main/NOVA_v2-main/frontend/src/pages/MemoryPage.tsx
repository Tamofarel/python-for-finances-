import { Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import type { MemoryRecord, Project } from "../api/types";
import { Empty, Field, Modal, Notice, formatTime } from "../components/ui";
import { useLoad } from "../hooks/useLoad";

const KIND_TEXT: Record<MemoryRecord["kind"], string> = { preference: "Preference", fact: "Fact", note: "Note" };

export function MemoryPage() {
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("all");
  const mem = useLoad(() => api.memory.list(q || undefined, scope), [q, scope]);
  const projects = useLoad<Project[]>(() => api.projects.list(true), []);
  const [form, setForm] = useState<{ kind: MemoryRecord["kind"]; content: string; project_id: string }>({ kind: "preference", content: "", project_id: "" });
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [editText, setEditText] = useState("");
  const [clearing, setClearing] = useState(false);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.memory.create({ kind: form.kind, content: form.content.trim(), project_id: form.project_id ? Number(form.project_id) : null });
      setForm({ ...form, content: "" });
      await mem.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function update(id: number, b: Partial<MemoryRecord>) {
    try {
      await api.memory.update(id, b);
      await mem.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const projectName = (id: number | null) => (id === null ? "General" : projects.data?.find((p) => p.id === id)?.name ?? `Project ${id}`);

  return (
    <div className="page narrow">
      <div className="page-head"><h1>Memory</h1></div>
      <p className="muted">NOVA only remembers what you save here. Enabled entries are added to NOVA's instructions as your preferences; project entries apply only inside that project. Nothing is inferred or saved automatically.</p>
      <form className="memory-form" onSubmit={add}>
        <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as MemoryRecord["kind"] })} aria-label="Type">
          <option value="preference">Preference</option>
          <option value="fact">Fact</option>
          <option value="note">Note</option>
        </select>
        <select value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })} aria-label="Scope">
          <option value="">General</option>
          {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="I prefer short answers with code examples" aria-label="What NOVA should remember" maxLength={5000} />
        <button type="submit" className="btn btn-primary btn-sm" disabled={!form.content.trim()}>Save to memory</button>
      </form>
      <div className="row top-gap">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memory" aria-label="Search memory" />
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Show">
          <option value="all">Everything</option>
          <option value="general">General only</option>
          <option value="project">Project memory only</option>
        </select>
        <span className="spacer" />
        <button type="button" className="btn btn-ghost btn-sm danger-text" onClick={() => setClearing(true)}>Clear memory…</button>
      </div>
      {(error || mem.error) && <Notice onDismiss={() => setError(null)}>{error || mem.error}</Notice>}
      {mem.data?.length === 0 ? (
        <Empty title={q ? "Nothing matches" : "Memory is empty"}>{q ? "Try another word." : "Save a preference or fact above."}</Empty>
      ) : (
        <ul className="memory-list">
          {mem.data?.map((m) => (
            <li key={m.id} className={m.enabled ? "" : "disabled"}>
              <div className="memory-meta small muted">
                {KIND_TEXT[m.kind]} in {projectName(m.project_id)}, saved {formatTime(m.created_at)} by {m.source}
              </div>
              <p>{m.content}</p>
              <div className="row">
                <label className="small toggle"><input type="checkbox" checked={m.enabled} onChange={(e) => void update(m.id, { enabled: e.target.checked })} /> Used by NOVA</label>
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => { setEditing(m); setEditText(m.content); }}>Edit</button>
                <button type="button" className="btn btn-ghost btn-icon" aria-label="Delete memory" onClick={async () => { await api.memory.remove(m.id); await mem.reload(); }}><Trash2 size={14} /></button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <Modal title="Edit memory" onClose={() => setEditing(null)} footer={
          <>
            <button type="button" className="btn" onClick={() => setEditing(null)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!editText.trim()} onClick={async () => { await update(editing.id, { content: editText.trim() }); setEditing(null); }}>Save memory</button>
          </>
        }>
          <Field label="Content"><textarea rows={4} value={editText} onChange={(e) => setEditText(e.target.value)} /></Field>
        </Modal>
      )}
      {clearing && (
        <Modal title="Clear memory?" onClose={() => setClearing(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setClearing(false)}>Cancel</button>
            <button type="button" className="btn btn-danger-outline" onClick={async () => { await api.memory.clear("general"); setClearing(false); await mem.reload(); }}>Clear general memory</button>
            <button type="button" className="btn btn-danger" onClick={async () => { await api.memory.clear("all"); setClearing(false); await mem.reload(); }}>Clear all memory</button>
          </>
        }>
          <p>Deleted entries are removed from NOVA's database immediately. Copies in your own backups of the <code>data</code> folder are not affected, and messages already sent to a cloud provider cannot be recalled.</p>
        </Modal>
      )}
    </div>
  );
}
