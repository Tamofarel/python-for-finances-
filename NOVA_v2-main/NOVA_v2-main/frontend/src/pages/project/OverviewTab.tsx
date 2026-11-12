import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Project } from "../../api/types";
import { Field, Modal, Notice, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { useChat } from "../../store/chat";

export function OverviewTab({ project, onChange }: { project: Project; onChange: (p: Project) => void }) {
  const [form, setForm] = useState({ name: project.name, description: project.description, instructions: project.instructions });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const convs = useLoad(() => api.conversations.list(undefined, project.id), [project.id]);
  const navigate = useNavigate();
  const dirty = form.name !== project.name || form.description !== project.description || form.instructions !== project.instructions;

  async function save() {
    setError(null);
    try {
      onChange(await api.projects.update(project.id, form));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggleArchive() {
    try {
      onChange(await api.projects.update(project.id, { archived: !project.archived }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function startConversation() {
    const conv = await useChat.getState().newConversation(project.id);
    navigate(`/c/${conv.id}`);
  }

  return (
    <div className="two-col">
      <section className="stack">
        <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={120} /></Field>
        <Field label="Description"><textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        <Field label="Instructions for NOVA" hint="Used as preferences in this project's conversations. Permissions are always enforced by the server, whatever these say.">
          <textarea rows={6} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
        </Field>
        {error && <Notice>{error}</Notice>}
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={!dirty || !form.name.trim()} onClick={save}>Save project</button>
          {saved && <span className="small ok-text" role="status">Saved</span>}
        </div>
        <p className="small muted">Workspace folder: <code>{project.workspace_path}</code></p>
      </section>
      <section className="stack">
        <div className="row">
          <h2 className="h3">Conversations</h2>
          <span className="spacer" />
          <button type="button" className="btn btn-sm" onClick={startConversation}>New conversation in this project</button>
        </div>
        {convs.data?.length ? (
          <ul className="plain-list">
            {convs.data.map((c) => <li key={c.id}><Link to={`/c/${c.id}`}>{c.title}</Link> <span className="small muted">{formatTime(c.updated_at)}</span></li>)}
          </ul>
        ) : <p className="muted small">No conversations are attached to this project yet.</p>}
        <h2 className="h3 top-gap">Manage</h2>
        <div className="row">
          <button type="button" className="btn btn-sm" onClick={toggleArchive}>{project.archived ? "Restore project" : "Archive project"}</button>
          <button type="button" className="btn btn-sm btn-danger-outline" onClick={() => setDeleting(true)}>Delete project</button>
        </div>
      </section>
      {deleting && (
        <Modal title="Delete project?" onClose={() => setDeleting(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setDeleting(false)}>Keep project</button>
            <button type="button" className="btn btn-danger" disabled={confirmName !== project.name} onClick={async () => {
              try { await api.projects.remove(project.id, confirmName); navigate("/projects"); } catch (e) { setError((e as Error).message); setDeleting(false); }
            }}>Delete project</button>
          </>
        }>
          <p>This deletes NOVA's records for the project: artifacts, change history and memory. Files in the workspace folder are kept on disk.</p>
          <Field label={`Type “${project.name}” to confirm`}>
            <input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
          </Field>
        </Modal>
      )}
    </div>
  );
}
