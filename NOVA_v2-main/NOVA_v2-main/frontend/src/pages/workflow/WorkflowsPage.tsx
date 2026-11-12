import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { Empty, Field, Notice, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { EXAMPLE_IDEA, EXAMPLE_TITLE } from "../command/panels";
import { TalkButton } from "../command/VoiceControls";

export function WorkflowsPage() {
  const list = useLoad(() => api.workflows.list(), []);
  const templates = useLoad(() => api.workflows.templates(), []);
  const [idea, setIdea] = useState("");
  const [template, setTemplate] = useState("auto");
  const [suggested, setSuggested] = useState<{ template: string; label: string; reason: string } | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const effective = template === "auto" ? suggested?.template ?? "general" : template;
  const tpl = templates.data?.find((t) => t.id === effective);

  // With "Let NOVA choose", show which kind of idea NOVA will use and why (a transparent keyword rule, no model).
  useEffect(() => {
    if (template !== "auto" || idea.trim().length < 10) {
      setSuggested(null);
      return;
    }
    const t = setTimeout(() => {
      api.workflows.suggest(idea.trim()).then(setSuggested).catch(() => setSuggested(null));
    }, 400);
    return () => clearTimeout(t);
  }, [idea, template]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const wf = await api.workflows.create({ idea: idea.trim(), template, title });
      navigate(`/workflows/${wf.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head"><h1>Ideas</h1></div>
      <div className="two-col">
        <section className="stack">
          <h2 className="h3">Start from an idea</h2>
          <p className="small muted">NOVA asks one question at a time, researches when a search provider is configured, drafts editable documents, and turns an approved proposal into tasks. You can leave and come back at any point.</p>
          <Field label="Your idea">
            <textarea rows={4} value={idea} onChange={(e) => { setIdea(e.target.value); setTitle(null); }} placeholder="What do you want to build or do?" />
          </Field>
          <div className="row wrap">
            <TalkButton compact label="Describe by voice" target={(t) => setIdea((x) => (x ? `${x} ${t}` : t))} />
            <button type="button" className="link-btn small" onClick={() => { setIdea(EXAMPLE_IDEA); setTemplate("auto"); setTitle(EXAMPLE_TITLE); }}>
              Use the example: plant-health photo app
            </button>
          </div>
          <Field label="Kind of idea" hint={template === "auto"
            ? (suggested ? `NOVA will use “${suggested.label}” because ${suggested.reason}.` : "NOVA picks the closest kind from your description.")
            : tpl?.description}>
            <select value={template} onChange={(e) => setTemplate(e.target.value)}>
              <option value="auto">Let NOVA choose</option>
              {templates.data?.map((t) => <option key={t.id} value={t.id}>{t.label}</option>) ?? <option value="general">General idea</option>}
            </select>
          </Field>
          {tpl && (
            <details>
              <summary>What NOVA will ask about ({tpl.topics.length} topics)</summary>
              <ol className="small">{tpl.topics.map((t) => <li key={t.id}>{t.question}{t.required ? "" : " (optional)"}</li>)}</ol>
            </details>
          )}
          {error && <Notice>{error}</Notice>}
          <div><button type="button" className="btn btn-primary" disabled={busy || idea.trim().length < 10} onClick={start}>{busy ? "Starting…" : "Start workflow"}</button></div>
        </section>
        <section className="stack">
          <h2 className="h3">Your workflows</h2>
          {list.error && <Notice>{list.error}</Notice>}
          {list.data?.length === 0 ? <Empty title="No ideas yet">Ideas you start appear here with their current stage.</Empty> : (
            <ul className="plain-list selectable">
              {list.data?.map((w) => (
                <li key={w.id}>
                  <Link className="wf-row" to={`/workflows/${w.id}`}>
                    <strong>{w.title}</strong>
                    <span className="small muted">
                      <span className={`badge badge-${w.status}`}>{w.status.replace("_", " ")}</span> {w.running ? `Running: ${w.current_stage_label}` : w.current_stage_label}, updated {formatTime(w.updated_at)}
                    </span>
                    {w.open_question && <span className="small">Waiting for you: {w.open_question.question}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
