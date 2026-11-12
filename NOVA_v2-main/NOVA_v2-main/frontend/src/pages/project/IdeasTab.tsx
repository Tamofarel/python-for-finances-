import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Artifact, Project, ResearchResponse } from "../../api/types";
import { Markdown } from "../../components/Markdown";
import { Field, Notice, Spinner, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { ArtifactEditor } from "./ArtifactEditor";

function ResearchBox({ project }: { project: Project }) {
  const status = useLoad(() => api.research.status(), []);
  const [query, setQuery] = useState("");
  const [brief, setBrief] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<ResearchResponse | null>(null);

  return (
    <section className="stack">
      <h2 className="h3">Research</h2>
      {status.data && !status.data.available && (
        <Notice kind="info">Web research is off. Configure a SearXNG instance (NOVA_RESEARCH_PROVIDER and NOVA_SEARXNG_URL) to enable it. NOVA never invents search results.</Notice>
      )}
      <form className="row" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try { setRes(await api.research.search(query.trim(), brief, project.id)); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
      }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="What do you need to know?" aria-label="Research question" disabled={!status.data?.available} />
        <label className="small toggle"><input type="checkbox" checked={brief} onChange={(e) => setBrief(e.target.checked)} /> Write a brief</label>
        <button type="submit" className="btn btn-sm" disabled={busy || query.trim().length < 2 || !status.data?.available}>{busy ? "Searching…" : "Search"}</button>
      </form>
      {error && <Notice>{error}</Notice>}
      {res && (
        <div className="stack">
          {res.results.length === 0 && <p className="muted">No results.</p>}
          <ol className="sources">
            {res.results.map((r) => (
              <li key={r.url}>
                <a href={r.url} target="_blank" rel="noopener noreferrer nofollow">{r.title || r.url}</a>
                <p className="small muted">{r.snippet}</p>
              </li>
            ))}
          </ol>
          {res.brief && <><h3 className="h4">Brief (saved to Design)</h3><Markdown>{res.brief}</Markdown></>}
        </div>
      )}
    </section>
  );
}

export function IdeasTab({ project }: { project: Project }) {
  const list = useLoad(() => api.artifacts.list(project.id, "idea_analysis"), [project.id]);
  const [idea, setIdea] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);

  useEffect(() => {
    if (!selected && list.data?.[0]) setSelected(list.data[0]);
  }, [list.data, selected]);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      const art = await api.artifacts.generate(project.id, "idea_analysis", idea.trim());
      setIdea("");
      await list.reload();
      setSelected(art);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="two-col wide-right">
      <div className="stack">
        <section className="stack">
          <Field label="Describe an idea" hint="NOVA writes a structured analysis: problem, users, assumptions, feasibility, risks, alternatives, a recommendation, milestones and the questions only you can answer.">
            <textarea rows={6} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="An app that helps students in Yaoundé find study groups by course and schedule…" />
          </Field>
          {error && <Notice>{error}</Notice>}
          <div><button type="button" className="btn btn-primary" disabled={busy || idea.trim().length < 3} onClick={analyze}>{busy ? "Analysing…" : "Analyse idea"}</button></div>
          {busy && <p className="small muted"><Spinner /> Waiting for the model. Long analyses can take a minute on local models.</p>}
        </section>
        <section>
          <h2 className="h3">Past analyses</h2>
          {list.data?.length === 0 && <p className="muted small">None yet.</p>}
          <ul className="plain-list selectable">
            {list.data?.map((a) => (
              <li key={a.id}>
                <button type="button" className={selected?.id === a.id ? "active" : ""} onClick={() => setSelected(a)}>
                  {a.title}<span className="small muted">{formatTime(a.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <ResearchBox project={project} />
      </div>
      <div>
        {selected ? (
          <ArtifactEditor artifact={selected} onSaved={(a) => { setSelected(a); void list.reload(); }} onDeleted={() => { setSelected(null); void list.reload(); }} />
        ) : (
          <p className="muted">Your analysis will appear here. You can edit it, export it to the workspace, or turn it into design documents.</p>
        )}
      </div>
    </div>
  );
}
