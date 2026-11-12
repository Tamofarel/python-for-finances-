import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Artifact, ArtifactKind, Project } from "../../api/types";
import { Field, Modal, Notice, Spinner } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { ArtifactEditor } from "./ArtifactEditor";
import { DESIGN_KINDS, KIND_LABELS } from "./kinds";

export function DesignTab({ project }: { project: Project }) {
  const [kindFilter, setKindFilter] = useState("");
  const [q, setQ] = useState("");
  const list = useLoad(() => api.artifacts.list(project.id, kindFilter || undefined, q || undefined), [project.id, kindFilter, q]);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [params] = useSearchParams();
  const wanted = Number(params.get("artifact")) || null;
  const [generating, setGenerating] = useState(false);
  const [gen, setGen] = useState<{ kind: ArtifactKind; input: string }>({ kind: "requirements", input: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!list.data) return;
    const linked = wanted ? list.data.find((a) => a.id === wanted) : undefined;
    if (linked && !selected) setSelected(linked);
    else if (!selected || !list.data.some((a) => a.id === selected.id)) setSelected(linked ?? list.data[0] ?? null);
  }, [list.data, selected, wanted]);

  async function run(fn: () => Promise<Artifact>) {
    setBusy(true);
    setError(null);
    try {
      const a = await fn();
      setGenerating(false);
      await list.reload();
      setSelected(a);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="split">
      <aside className="split-list" aria-label="Artifacts">
        <div className="row">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setGenerating(true)}><Plus size={14} aria-hidden /> Generate document</button>
          <button type="button" className="btn btn-sm" onClick={() => run(() => api.artifacts.create(project.id, { kind: "note", title: "Untitled note", content: "" }))}>Blank note</button>
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search documents" aria-label="Search documents" />
        <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} aria-label="Filter by type">
          <option value="">All types</option>
          {Object.entries(KIND_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {list.error && <Notice>{list.error}</Notice>}
        {list.loading && !list.data && <Spinner />}
        {list.data?.length === 0 && <p className="muted small pad">No documents yet. Generate requirements, architecture, data models and more from a short description.</p>}
        <ul className="plain-list selectable">
          {list.data?.map((a) => (
            <li key={a.id}>
              <button type="button" className={selected?.id === a.id ? "active" : ""} onClick={() => setSelected(a)}>
                {a.title}<span className="small muted">{KIND_LABELS[a.kind]}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="split-main">
        {error && !generating && <Notice onDismiss={() => setError(null)}>{error}</Notice>}
        {selected ? (
          <ArtifactEditor artifact={selected} onSaved={(a) => { setSelected(a); void list.reload(); }} onDeleted={() => { setSelected(null); void list.reload(); }} />
        ) : (
          <p className="muted">Select or generate a document.</p>
        )}
      </section>
      {generating && (
        <Modal title="Generate a design document" onClose={() => setGenerating(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setGenerating(false)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={busy || gen.input.trim().length < 3}
              onClick={() => run(() => api.artifacts.generate(project.id, gen.kind, gen.input.trim()))}>
              {busy ? "Generating…" : "Generate"}
            </button>
          </>
        }>
          <div className="stack">
            <Field label="Document type">
              <select value={gen.kind} onChange={(e) => setGen({ ...gen, kind: e.target.value as ArtifactKind })}>
                {DESIGN_KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
              </select>
            </Field>
            <Field label="What is it about?" hint="Paste an idea analysis, a feature description, or notes. Architecture and data models include Mermaid diagrams.">
              <textarea rows={7} value={gen.input} onChange={(e) => setGen({ ...gen, input: e.target.value })} />
            </Field>
            {error && <Notice>{error}</Notice>}
          </div>
        </Modal>
      )}
    </div>
  );
}
