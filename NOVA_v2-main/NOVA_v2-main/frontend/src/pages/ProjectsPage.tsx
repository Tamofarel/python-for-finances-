import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Empty, Field, Modal, Notice, Spinner, formatTime } from "../components/ui";
import { useLoad } from "../hooks/useLoad";

export function ProjectsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, error, loading } = useLoad(() => api.projects.list(showArchived), [showArchived]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", instructions: "", workspace_path: "" });
  const [formError, setFormError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function create(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      const p = await api.projects.create({ ...form, workspace_path: form.workspace_path.trim() || null });
      navigate(`/projects/${p.id}`);
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Projects</h1>
        <label className="small toggle"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived</label>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)}><Plus size={15} aria-hidden /> New project</button>
      </div>
      {error && <Notice>{error}</Notice>}
      {loading && !data && <p><Spinner /> Loading projects…</p>}
      {data && data.length === 0 && (
        <Empty title="No projects yet" action={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>Create your first project</button>}>
          A project groups conversations, files in a workspace folder, design documents and approved commands.
        </Empty>
      )}
      {data && data.length > 0 && (
        <ul className="project-grid">
          {data.map((p) => (
            <li key={p.id} className={p.archived ? "archived" : ""}>
              <Link to={`/projects/${p.id}`}>
                <h2>{p.name}</h2>
                <p className="muted">{p.description || "No description."}</p>
                <span className="small muted">{p.archived ? "Archived" : `Updated ${formatTime(p.updated_at)}`}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {creating && (
        <Modal title="New project" onClose={() => setCreating(false)}>
          <form className="stack" onSubmit={create}>
            <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={120} /></Field>
            <Field label="Description"><textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} /></Field>
            <Field label="Instructions for NOVA" hint="Preferences NOVA follows in this project's conversations. They never override security rules.">
              <textarea value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} rows={3} />
            </Field>
            <Field label="Workspace folder (optional)" hint="Leave empty to create one in NOVA's data folder. Other folders must be inside NOVA_ALLOWED_WORKSPACE_ROOTS.">
              <input value={form.workspace_path} onChange={(e) => setForm({ ...form, workspace_path: e.target.value })} placeholder="C:\Users\you\Projects\my-app" />
            </Field>
            {formError && <Notice>{formError}</Notice>}
            <div className="row-end">
              <button type="button" className="btn" onClick={() => setCreating(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary">Create project</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
