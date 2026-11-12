// What NOVA actually did for a reply: one line per tool call, with the real, verified (or honestly unverified)
// outcome reported by NOVA's backend, and the next step when something blocks it.

import { Link } from "react-router-dom";
import type { ActionRecord, ActionStatus } from "../api/types";
import { Spinner } from "./ui";

const TOOL_LABELS: Record<string, string> = {
  spotify_control: "Spotify",
  open_app: "App",
  open_website: "Website",
  browser_open: "Browser",
  browser_inspect: "Browser",
  browser_search: "Browser search",
  browser_open_link: "Browser link",
  open_file: "File",
  open_project: "Project",
  media_control: "Media keys",
  run_project_command: "Command",
  home_status: "Smart home",
  home_control: "Smart home",
};

const STATUS_TEXT: Record<ActionStatus, string> = {
  succeeded: "Done",
  unverified: "Not confirmed",
  needs_input: "Needs your choice",
  needs_authorization: "Needs a connection",
  awaiting_approval: "Waiting for your approval",
  unavailable: "Unavailable",
  denied: "Refused",
  failed: "Failed",
  timed_out: "Timed out",
  cancelled: "Stopped",
};

const BADGE: Partial<Record<ActionStatus, string>> = {
  succeeded: "succeeded",
  unverified: "pending",
  needs_input: "pending",
  needs_authorization: "pending",
  awaiting_approval: "awaiting_approval",
  cancelled: "cancelled",
};

const SPOTIFY_ACTIONS: Record<string, string> = {
  open: "Open Spotify", pause: "Pause", resume: "Resume", play_pause: "Play / pause", next: "Next track",
  previous: "Previous track", volume_up: "Volume up", volume_down: "Volume down",
};

function str(v: unknown): string {
  return v === undefined || v === null ? "" : String(v);
}

export function describeCall(a: ActionRecord): string {
  const g = a.arguments ?? {};
  switch (a.tool) {
    case "spotify_control":
      if (g.action === "play_track") return `Play “${str(g.query)}”${g.artist ? ` by ${str(g.artist)}` : ""}`;
      if (g.action === "play_playlist") return `Play playlist “${str(g.query)}”`;
      if (g.action === "search") return `Search “${str(g.query)}”`;
      return SPOTIFY_ACTIONS[str(g.action)] ?? str(g.action);
    case "open_app":
      return `Open ${str(g.app)}`;
    case "open_website":
      return `Open ${str(g.url)}`;
    case "browser_open":
      return `Open ${str(g.url)} in NOVA's isolated browser`;
    case "browser_inspect":
      return "Inspect the current browser page";
    case "browser_search":
      return `Find “${str(g.query)}” on the current page`;
    case "browser_open_link":
      return `Open the link “${str(g.text)}”`;
    case "open_file":
      return `Open ${str(g.path)}`;
    case "open_project":
      return `Open the project in ${g.app === "vscode" ? "VS Code" : "File Explorer"}`;
    case "media_control":
      return str(g.action).replace(/_/g, " ");
    case "run_project_command":
      return `Run ${Array.isArray(g.argv) ? g.argv.join(" ") : ""}`;
    case "home_status":
      return `Check ${str(g.entity_id)}`;
    case "home_control":
      return `${str(g.action).replace(/_/g, " ")} ${str(g.entity_id)}`;
    default:
      return a.tool;
  }
}

function Candidates({ items }: { items: unknown }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <ol className="small action-choices">
      {items.map((c, i) => {
        const item = c as { name?: string; artists?: string[] };
        return <li key={i}>{item.name}{item.artists?.length ? ` — ${item.artists.join(", ")}` : ""}</li>;
      })}
    </ol>
  );
}

function ActionItem({ a, reply }: { a: ActionRecord; reply: string }) {
  const running = a.phase === "started";
  const status = a.status;
  const label = running ? "Running…" : status === "succeeded" ? (a.verified ? "Done · verified" : "Done") : status ? STATUS_TEXT[status] : "";
  const badge = running ? "running" : status ? BADGE[status] ?? "failed" : "failed";
  const connect = typeof a.data?.connect_url === "string" ? a.data.connect_url : null;
  return (
    <li className={`action action-${running ? "running" : status}`}>
      <div className="action-head">
        <strong>{TOOL_LABELS[a.tool] ?? a.tool}</strong>
        <span className="action-what">{describeCall(a)}</span>
        <span className={`badge badge-${badge}`}>{running && <Spinner label="Running" />} {label}</span>
      </div>
      {/* When NOVA's reply is this very message (its own report), it is not repeated in the card. */}
      {a.message && !reply.includes(a.message) && <p className="small action-msg">{a.message}</p>}
      <Candidates items={a.data?.candidates} />
      {(connect || status === "awaiting_approval") && (
        <div className="row">
          {connect && <a className="btn btn-sm" href={connect}>{a.tool === "spotify_control" ? "Connect Spotify" : "Connect"}</a>}
          {status === "awaiting_approval" && <Link className="btn btn-sm" to="/approvals">Review in Approvals</Link>}
        </div>
      )}
    </li>
  );
}

export function ActionList({ actions, reply = "" }: { actions: ActionRecord[]; reply?: string }) {
  return (
    <ul className="action-list" aria-label="What NOVA did">
      {actions.map((a) => <ActionItem key={a.id} a={a} reply={reply} />)}
    </ul>
  );
}
