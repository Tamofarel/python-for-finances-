// Actions: what NOVA can do on this computer and on devices, and the emergency stop.

import { OctagonX, Play } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { ToolInfo } from "../../api/types";
import { Notice, Spinner } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";

const LABELS: Record<string, string> = {
  spotify_control: "Spotify",
  open_app: "Open approved apps",
  open_website: "Open websites",
  browser_open: "Isolated browser",
  browser_inspect: "Read the browser page",
  browser_search: "Search the browser page",
  browser_open_link: "Open a browser link",
  open_file: "Open project files",
  open_project: "Open the project in VS Code or Explorer",
  media_control: "System media keys",
  run_project_command: "Project commands (after approval)",
  home_status: "Smart-home status",
  home_control: "Smart-home control",
};

const RISK: Record<ToolInfo["risk"], string> = {
  observe: "Observe only",
  reversible: "Reversible",
  "safety-critical": "Needs your approval",
};

export function ActionsSettings() {
  const tools = useLoad(() => api.tools.list(), []);
  const control = useLoad(() => api.actions.control(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await control.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const paused = control.data?.paused ?? false;
  return (
    <div className="stack settings-section">
      <section className="stack-sm">
        <h2 className="h3">Emergency stop</h2>
        {error && <Notice>{error}</Notice>}
        {paused ? (
          <div className="estop">
            <Notice kind="warn">
              Device control is paused. NOVA refuses to control apps and devices until you resume; it can still read
              device states. {stopped}
            </Notice>
            <button type="button" className="btn" disabled={busy} onClick={() => void run(async () => {
              await api.actions.resume();
              setStopped(null);
            })}>
              <Play size={15} aria-hidden /> Resume device control
            </button>
          </div>
        ) : (
          <div className="estop">
            <button type="button" className="btn btn-danger" disabled={busy || !control.data} onClick={() => void run(async () => {
              const r = await api.actions.emergencyStop();
              setStopped(r.cancelled_actions ? `${r.cancelled_actions} running action(s) were stopped.` : null);
            })}>
              <OctagonX size={16} aria-hidden /> Emergency stop
            </button>
            <p className="small muted">
              Stops running actions and commands at once, rejects device requests waiting for approval, and pauses device
              control until you resume it. Your devices' own switches, buttons and apps always keep working.
              {control.data && control.data.running > 0 && ` ${control.data.running} action(s) running now.`}
            </p>
          </div>
        )}
      </section>

      <section className="stack-sm">
        <h2 className="h3">What NOVA can do</h2>
        <p className="small muted">
          Ask in words, typed or spoken, for example “play Cœur De Pirate by Tiakola” or “open the calculator”. NOVA picks
          one of these tools, checks it, runs it and reports the result it observed. Safety-critical actions wait for your
          approval on screen; voice never approves them.
        </p>
        {tools.error && <Notice>{tools.error}</Notice>}
        {!tools.data && !tools.error && <p className="muted"><Spinner /> Loading…</p>}
        {tools.data && (
          <table className="table">
            <thead><tr><th>Action</th><th>Risk</th><th>Status</th></tr></thead>
            <tbody>
              {tools.data.map((t) => (
                <tr key={t.name}>
                  <td>{LABELS[t.name] ?? t.name}</td>
                  <td className="small">{RISK[t.risk]}</td>
                  <td className="small">
                    {t.enabled ? <span className="badge badge-succeeded">Ready</span> : <span className="badge">Off</span>}
                    {t.unavailable_reason && !t.enabled && <div className="muted">{t.unavailable_reason}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small muted">
          Configure in <code>.env</code>: <code>NOVA_DESKTOP_CONTROL_ENABLED</code>, <code>NOVA_DESKTOP_APPS</code>,
          {" "}<code>NOVA_BROWSER_ALLOWED_HOSTS</code> and the <code>NOVA_HOME_ASSISTANT_*</code> settings, then restart NOVA.
          Connect Spotify under Settings → Voice. Details: docs/ACTIONS.md.
        </p>
      </section>
    </div>
  );
}
