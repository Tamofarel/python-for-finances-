import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProviderStatus } from "../api/types";
import { useProviders } from "../store/providers";
import { Notice, Spinner, StatusDot } from "../components/ui";
import { useChat } from "../store/chat";

function stateOf(p: ProviderStatus): { dot: "ok" | "warn" | "off"; text: string } {
  if (!p.configured) return { dot: "off", text: "Not configured" };
  if (!p.reachable) return { dot: "warn", text: "Configured but not reachable" };
  if (p.error) return { dot: "warn", text: "Reachable, needs attention" };
  return { dot: "ok", text: "Connected" };
}

export function ProvidersPage() {
  const { data, error, reload } = useProviders();
  const [checking, setChecking] = useState(false);
  const [choice, setChoice] = useState<{ provider: string; model: string } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (data?.selected && !choice) setChoice(data.selected);
  }, [data, choice]);

  async function recheck() {
    setChecking(true);
    await reload();
    setChecking(false);
  }

  async function save() {
    if (!choice) return;
    setSaveError(null);
    try {
      await api.providers.select(choice);
      useChat.getState().setOverride(null);
      setSaved(`NOVA will use ${choice.model} for new messages.`);
      await reload();
    } catch (e) {
      setSaveError((e as Error).message);
    }
  }

  const current = data?.providers.find((p) => p.name === choice?.provider);

  return (
    <div className="page narrow">
      <div className="page-head">
        <h1>Models and providers</h1>
        <button type="button" className="btn btn-sm" onClick={recheck} disabled={checking}>
          <RefreshCw size={14} aria-hidden /> {checking ? "Checking…" : "Check again"}
        </button>
      </div>
      <p className="muted">Status comes from a live check each time this page loads. A provider counts as connected only when it actually answers.</p>
      {error && <Notice>{error}</Notice>}
      {!data && !error && <p><Spinner /> Checking providers…</p>}
      {data && (
        <div className="provider-list">
          {data.providers.map((p) => {
            const s = stateOf(p);
            return (
              <section key={p.name} className={`provider ${choice?.provider === p.name ? "selected" : ""}`}>
                <header>
                  <label className="radio-row">
                    <input type="radio" name="provider" checked={choice?.provider === p.name}
                      onChange={() => setChoice({ provider: p.name, model: p.models.includes(p.default_model) || !p.models.length ? p.default_model : p.models[0]! })} />
                    <strong>{p.label}</strong>
                  </label>
                  <span className="provider-state"><StatusDot state={s.dot} /> {s.text}</span>
                </header>
                {p.error && <p className="small warn-text">{p.error}</p>}
                <p className="small muted">
                  {p.kind === "local" ? "Runs on this computer. Nothing leaves your machine." : "Cloud service. Messages are sent to the provider's API."}
                  {" "}Default model: <code>{p.default_model}</code>.
                  {p.models.length > 0 && ` ${p.models.length} model${p.models.length > 1 ? "s" : ""} reported.`}
                </p>
              </section>
            );
          })}
        </div>
      )}
      {choice && (
        <div className="stack top-gap">
          <label className="field">
            <span className="field-label">Model</span>
            {current && current.models.length > 0 ? (
              <select value={choice.model} onChange={(e) => setChoice({ ...choice, model: e.target.value })}>
                {!current.models.includes(choice.model) && <option value={choice.model}>{choice.model} (not reported by provider)</option>}
                {current.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input value={choice.model} onChange={(e) => setChoice({ ...choice, model: e.target.value })} aria-describedby="model-hint" />
            )}
            <span className="field-hint" id="model-hint">The model name exactly as the provider expects it.</span>
          </label>
          {saveError && <Notice>{saveError}</Notice>}
          {saved && <Notice kind="ok">{saved}</Notice>}
          <div><button type="button" className="btn btn-primary" onClick={save} disabled={!choice.model.trim()}>Use this model</button></div>
        </div>
      )}
      <details className="top-gap">
        <summary>How to configure providers</summary>
        <div className="prose small">
          <p><strong>Ollama (local):</strong> install Ollama, run <code>ollama pull llama3.2</code> yourself (about 2 GB), then set <code>NOVA_OLLAMA_BASE_URL</code> and <code>NOVA_OLLAMA_MODEL</code> in <code>.env</code>. NOVA never downloads models.</p>
          <p><strong>Anthropic (cloud):</strong> set <code>NOVA_ANTHROPIC_API_KEY</code> in <code>.env</code> and restart the backend. The key stays on the backend and is never sent to this page.</p>
        </div>
      </details>
    </div>
  );
}
