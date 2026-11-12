import {
  Brain,
  CircleDot,
  FolderKanban,
  History,
  Lightbulb,
  LogOut,
  Menu,
  MessageSquare,
  PanelRight,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { ActionRequest, Task } from "../api/types";
import { useProviders } from "../store/providers";
import { useChat } from "../store/chat";
import { useSession } from "../store/session";
import { ApprovalDialog, describeAction } from "./Approval";
import { type AssistantState, STATE_LABELS } from "../assistant/machine";
import type { Presence } from "../store/chat";
import { useAssistantWiring } from "../assistant/wiring";
import { useAssistant } from "../store/assistant";
import { useVoice } from "../store/voice";
import { Orb } from "./Orb";
import { StatusDot } from "./ui";
import { MicStatusButton, ThemeToggle } from "./VoiceStatus";

const PRESENCE: Partial<Record<AssistantState, Presence>> = {
  wake_detected: "listening", listening: "listening", transcribing: "listening", understanding: "thinking",
  thinking: "thinking", responding: "streaming", speaking: "speaking", researching: "thinking", designing: "thinking",
  executing: "thinking",
};

const NAV = [
  { to: "/", label: "Command", icon: CircleDot, end: true },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/workflows", label: "Ideas", icon: Lightbulb },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/history", label: "History", icon: History },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/providers", label: "Models", icon: SlidersHorizontal },
  { to: "/settings", label: "Settings", icon: Settings },
];

function ActivityPanel({ onClose }: { onClose: () => void }) {
  const [pending, setPending] = useState<ActionRequest[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [open, setOpen] = useState<ActionRequest | null>(null);
  const current = useChat((s) => s.current);
  const refresh = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([api.actions.list("pending"), api.tasks.list()]);
      setPending(p);
      setTasks(t.slice(0, 6));
    } catch {
      /* panel is best-effort */
    }
  }, []);
  useEffect(() => {
    void refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <aside className="context-panel" aria-label="Activity">
      <div className="panel-head">
        <h2>Activity</h2>
        <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Hide activity panel">
          <PanelRight size={16} />
        </button>
      </div>
      {current && (
        <section>
          <h3>Current conversation</h3>
          <p className="small">{current.title}</p>
          {current.project_id && <NavLink className="small" to={`/projects/${current.project_id}`}>Open its project</NavLink>}
        </section>
      )}
      <section>
        <h3>Waiting for your approval</h3>
        {pending.length === 0 ? (
          <p className="muted small">Nothing is waiting.</p>
        ) : (
          <ul className="plain-list">
            {pending.map((a) => (
              <li key={a.id}>
                <button type="button" className="link-btn" onClick={() => setOpen(a)}>{describeAction(a).what.replace(/`/g, "")}</button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h3>Recent tasks</h3>
        {tasks.length === 0 ? (
          <p className="muted small">No commands have run yet.</p>
        ) : (
          <ul className="plain-list">
            {tasks.map((t) => (
              <li key={t.id} className="small">
                <span className={`badge badge-${t.status}`}>{t.status.replace("_", " ")}</span> <code>{(t.command ?? []).join(" ")}</code>
              </li>
            ))}
          </ul>
        )}
      </section>
      {open && <ApprovalDialog action={open} onClose={() => setOpen(null)} onDecided={refresh} />}
    </aside>
  );
}

export function Layout() {
  const user = useSession((s) => s.user);
  const logout = useSession((s) => s.logout);
  useAssistantWiring();
  const assistantState = useAssistant((s) => s.state);
  const pendingApprovals = useAssistant((s) => s.snapshot.context.pendingApprovals);
  const presence: Presence = PRESENCE[assistantState] ?? "idle";
  const override = useChat((s) => s.override);
  const { data: providers } = useProviders();
  const [panel, setPanel] = useState(false); // the activity panel is opt-in; screens show context where it matters
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setNavOpen(false), [location.pathname]);
  const onCommand = location.pathname === "/";

  const sel = override ?? providers?.selected ?? null;
  const st = providers?.providers.find((p) => p.name === sel?.provider);
  const modelState: "ok" | "warn" | "off" = !st ? "off" : st.reachable && !st.error ? "ok" : st.configured ? "warn" : "off";

  return (
    <div className={`shell ${panel && !onCommand ? "with-panel" : ""} ${onCommand ? "on-command" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <nav className={`rail ${navOpen ? "open" : ""}`} aria-label="Main">
        <NavLink to="/" className="rail-brand" aria-label={`NOVA — ${STATE_LABELS[assistantState]}`} data-tip={STATE_LABELS[assistantState]}>
          <Orb presence={presence} size={30} />
        </NavLink>
        <ul>
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink to={to} end={end} className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`} data-tip={label} aria-label={
                to === "/approvals" && pendingApprovals ? `${label} (${pendingApprovals} waiting)` : label}>
                <Icon size={19} aria-hidden />
                <span className="rail-label">{label}</span>
                {to === "/approvals" && pendingApprovals > 0 && <span className="rail-badge" aria-hidden>{pendingApprovals}</span>}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="rail-foot">
          <MicStatusButton />
          <ThemeToggle />
          <button type="button" className="rail-btn" onClick={() => { useVoice.getState().release(); void logout(); }}
            aria-label={`Sign out ${user?.display_name || user?.username || ""}`.trim()} data-tip={`Sign out (${user?.display_name || user?.username})`}>
            <LogOut size={18} aria-hidden />
          </button>
        </div>
      </nav>
      {navOpen && <button type="button" className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      <div className="main-col">
        <header className={`topbar ${onCommand ? "topbar-bare" : ""}`}>
          <button type="button" className="btn btn-ghost btn-icon nav-toggle" onClick={() => setNavOpen((v) => !v)} aria-label="Open navigation" aria-expanded={navOpen}>
            <Menu size={18} />
          </button>
          {!onCommand && (
            <NavLink to="/providers" className="model-chip" title={st?.error ?? undefined}>
              <StatusDot state={modelState} />
              {sel ? (
                <>
                  <span>{st?.label ?? sel.provider}</span>
                  <span className="muted">{sel.model}</span>
                </>
              ) : (
                <span>Checking models…</span>
              )}
            </NavLink>
          )}
          {!onCommand && st?.error && <span className="small warn-text topbar-warn">{st.error}</span>}
          <span className="spacer" />
          {!onCommand && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPanel((v) => !v)} aria-pressed={panel}>
            <PanelRight size={15} aria-hidden /> Activity
          </button>}
        </header>
        <main id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      {panel && !onCommand && <ActivityPanel onClose={() => setPanel(false)} />}
    </div>
  );
}
