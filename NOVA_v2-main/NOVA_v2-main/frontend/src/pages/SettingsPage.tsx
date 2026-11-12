import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AuditEvent } from "../api/types";
import { Field, Notice, formatTime } from "../components/ui";
import { useLoad } from "../hooks/useLoad";
import { useSession } from "../store/session";
import { ActionsSettings } from "./settings/ActionsSettings";
import { AppearanceSettings, ProactivitySettings } from "./settings/AppearanceSettings";
import { CapabilitiesList } from "./settings/CapabilitiesList";
import { VoiceSettings } from "./settings/VoiceSettings";

const TABS = [
  { id: "appearance", label: "Appearance" },
  { id: "voice", label: "Voice" },
  { id: "actions", label: "Actions" },
  { id: "suggestions", label: "Suggestions" },
  { id: "capabilities", label: "Capabilities" },
  { id: "security", label: "Account and security" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function AuditTable({ events, empty }: { events: AuditEvent[]; empty: string }) {
  if (!events.length) return <p className="muted">{empty}</p>;
  return (
    <table className="table audit">
      <thead><tr><th>When</th><th>Event</th><th>Target</th><th>Outcome</th><th>Details</th></tr></thead>
      <tbody>
        {events.map((e) => (
          <tr key={e.id}>
            <td className="small muted nowrap">{formatTime(e.created_at)}</td>
            <td><code>{e.action}</code></td>
            <td className="small break">{e.target}</td>
            <td><span className={`badge badge-${e.outcome}`}>{e.outcome}</span></td>
            <td className="small break">{Object.keys(e.details).length ? JSON.stringify(e.details) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PasswordForm() {
  const accept = useSession((s) => s.accept);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      accept(await api.auth.changePassword(cur, next));
      setCur("");
      setNext("");
      setMsg({ kind: "ok", text: "Password changed. Other sessions were signed out." });
    } catch (err) {
      setMsg({ kind: "error", text: (err as Error).message });
    }
  }
  return (
    <form className="stack" onSubmit={submit}>
      <Field label="Current password"><input type="password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" /></Field>
      <Field label="New password" hint="At least 10 characters."><input type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} autoComplete="new-password" /></Field>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div><button type="submit" className="btn btn-primary btn-sm">Change password</button></div>
    </form>
  );
}

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "appearance");
  return (
    <div className="page settings">
      <div className="page-head"><h1>Settings</h1></div>
      <div className="settings-layout">
        <nav className="settings-tabs" aria-label="Settings sections">
          {TABS.map((t) => (
            <button key={t.id} type="button" className={tab === t.id ? "active" : ""} aria-current={tab === t.id ? "page" : undefined}
              onClick={() => setParams({ tab: t.id }, { replace: true })}>{t.label}</button>
          ))}
        </nav>
        <div className="settings-body">
          {tab === "appearance" && <AppearanceSettings />}
          {tab === "voice" && <VoiceSettings />}
          {tab === "actions" && <ActionsSettings />}
          {tab === "suggestions" && <ProactivitySettings />}
          {tab === "capabilities" && <CapabilitiesList />}
          {tab === "security" && <SecuritySettings />}
        </div>
      </div>
    </div>
  );
}

function SecuritySettings() {
  const user = useSession((s) => s.user);
  const [filter, setFilter] = useState("");
  const audit = useLoad(() => api.audit.list(filter || undefined), [filter]);

  return (
    <div className="stack settings-section">
      <div className="two-col">
        <section className="stack">
          <h2 className="h3">Account</h2>
          <p className="small">Signed in as <strong>{user?.username}</strong>{user?.is_owner ? ", owner of this NOVA installation" : ""}.</p>
          <PasswordForm />
        </section>
        <section className="stack">
          <h2 className="h3">Permissions</h2>
          <table className="table perm">
            <tbody>
              <tr><th scope="row">Read-only</th><td>Reading project files, listing folders, searching history. Runs without asking, only inside a project's workspace.</td></tr>
              <tr><th scope="row">Reversible changes</th><td>Edits to files and exported documents become proposals with a diff. They're written only when you apply them, and can be reverted. Moving or renaming a file needs your approval.</td></tr>
              <tr><th scope="row">Consequential actions</th><td>Running commands and deleting files need explicit confirmation of the exact action, once. Commands must match the allowlist (<code>NOVA_COMMAND_ALLOWLIST</code>).</td></tr>
            </tbody>
          </table>
          <p className="small muted">These rules are enforced by the backend. Project instructions, memory, voice commands and model output cannot change them. Approvals are always given on screen, never by voice.</p>
        </section>
      </div>
      <section className="stack top-gap">
        <div className="row">
          <h2 className="h3">Audit history</h2>
          <span className="spacer" />
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter audit events">
            <option value="">All events</option>
            <option value="auth">Sign-ins and account</option>
            <option value="change">File changes</option>
            <option value="command">Commands</option>
            <option value="delete">Deletions</option>
            <option value="memory">Memory</option>
            <option value="project">Projects</option>
            <option value="voice">Microphone</option>
            <option value="preferences">Preferences</option>
          </select>
        </div>
        {audit.error && <Notice>{audit.error}</Notice>}
        <AuditTable events={audit.data ?? []} empty="No events recorded yet." />
      </section>
    </div>
  );
}
