// Context panels of the command center. Each one appears only when it has something relevant to show, and every
// panel shows real backend data with a link to the full screen.

import { FileText, FlaskConical, FolderOpen, ListChecks, MessageSquareText, ShieldCheck } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import type { ActionRequest, Message, Project, Suggestion, WorkItem, WorkflowDetail } from "../../api/types";
import { ApprovalDialog, describeAction } from "../../components/Approval";
import { Markdown } from "../../components/Markdown";

export function Panel({ title, icon, action, children, className = "" }: { title: string; icon: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`holo-panel ${className}`} aria-label={title}>
      <header>
        <span className="holo-panel-icon" aria-hidden>{icon}</span>
        <h2>{title}</h2>
        {action}
      </header>
      <div className="holo-panel-body">{children}</div>
    </section>
  );
}

export function suggestionHref(s: Suggestion): string {
  const t = s.target;
  if (t.type === "approvals") return "/approvals";
  if (t.type === "workflow") return `/workflows/${t.id}`;
  if (t.type === "work") return `/projects/${t.project_id}/work`;
  return `/projects/${t.project_id}/changes`;
}

/** Up to three next-step suggestions computed by the backend from real project state (no background monitoring).
 *  An idea's open question is answered right here; everything else opens its screen. */
export function SuggestionChips({ items, onFocusWorkflow }: { items: Suggestion[]; onFocusWorkflow?: (id: number) => void }) {
  if (!items.length) return null;
  return (
    <nav className="chips" aria-label="Suggested next steps">
      {items.slice(0, 3).map((s, i) =>
        s.kind === "question" && s.target.type === "workflow" && s.target.id && onFocusWorkflow ? (
          <button key={i} type="button" className={`chip chip-${s.kind}`} title={s.detail || undefined}
            onClick={() => onFocusWorkflow(s.target.id!)}>{s.title}</button>
        ) : (
          <Link key={i} className={`chip chip-${s.kind}`} to={suggestionHref(s)} title={s.detail || undefined}>{s.title}</Link>
        ))}
    </nav>
  );
}

export function ApprovalsPanel({ items, onDecided }: { items: ActionRequest[]; onDecided: () => void }) {
  const [open, setOpen] = useState<ActionRequest | null>(null);
  return (
    <Panel title={items.length === 1 ? "1 action needs your approval" : `${items.length} actions need your approval`}
      icon={<ShieldCheck size={15} />} className="attention" action={<Link className="small" to="/approvals">All</Link>}>
      <ul className="plain-list">
        {items.slice(0, 4).map((a) => (
          <li key={a.id}><button type="button" className="link-btn" onClick={() => setOpen(a)}>{describeAction(a).what.replace(/`/g, "")}</button></li>
        ))}
      </ul>
      {open && <ApprovalDialog action={open} onClose={() => setOpen(null)} onDecided={onDecided} />}
    </Panel>
  );
}

const ACTIVE: WorkItem["status"][] = ["in_progress", "awaiting_approval", "failed", "blocked"];

/** Only tasks that are moving or stuck; the full board is one click away. */
export function activeTasks(items: WorkItem[] | null): WorkItem[] {
  return (items ?? []).filter((i) => ACTIVE.includes(i.status))
    .sort((a, b) => ACTIVE.indexOf(a.status) - ACTIVE.indexOf(b.status)).slice(0, 5);
}

export function TasksPanel({ items }: { items: WorkItem[] }) {
  return (
    <Panel title="Tasks in motion" icon={<ListChecks size={15} />}>
      <ul className="plain-list">
        {items.map((i) => (
          <li key={i.id} className="task-line">
            <span className={`badge badge-${i.status}`}>{i.status.replace("_", " ")}</span>
            <Link to={`/projects/${i.project_id}/work`}>{i.title}</Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** Research findings and documents of the idea in focus. */
export function WorkflowDocsPanel({ workflow }: { workflow: WorkflowDetail }) {
  const sourced = workflow.research.filter((r) => r.status === "sourced").length;
  const docs = workflow.stages.flatMap((s) => s.artifacts);
  if (!docs.length && !workflow.research.length) return null;
  return (
    <Panel title="Research and documents" icon={<FlaskConical size={15} />}
      action={<Link className="small" to={`/workflows/${workflow.id}`}>Open</Link>}>
      {workflow.research.length > 0 && (
        <p className="small muted">
          {sourced} of {workflow.research.length} research questions have real sources
          {sourced < workflow.research.length ? "; the others list where to look." : "."}
        </p>
      )}
      {docs.length > 0 && (
        <ul className="plain-list doc-list">
          {docs.slice(-6).map((d) => (
            <li key={d.id}>
              <Link to={`/projects/${workflow.project_id}/design?artifact=${d.id}`}><FileText size={13} aria-hidden /> {d.title}</Link>
              <span className="small muted">{d.created_by === "ai" ? "AI draft — review it" : "from your answers"}{d.edited ? ", edited" : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function ProjectPanel({ project }: { project: Project }) {
  return (
    <Panel title={project.name} icon={<FolderOpen size={15} />} action={<Link className="small" to={`/projects/${project.id}`}>Open</Link>}>
      {project.description && <p className="small muted clamp-3">{project.description}</p>}
      <p className="small link-row"><Link to={`/projects/${project.id}/files`}>Files</Link><Link to={`/projects/${project.id}/design`}>Documents</Link><Link to={`/projects/${project.id}/work`}>Tasks</Link></p>
    </Panel>
  );
}

/** A long reply (or one with code, tables or diagrams) is shown in full here; the stage keeps a short caption. */
export function ReplyPanel({ message, conversationId }: { message: Message; conversationId: number }) {
  return (
    <Panel title="NOVA's reply" icon={<MessageSquareText size={15} />} className="reply-panel"
      action={<Link className="small" to={`/c/${conversationId}`}>Conversation</Link>}>
      <div className="prose"><Markdown>{message.content}</Markdown></div>
    </Panel>
  );
}

export const EXAMPLE_TITLE = "Plant-health photo app";
export const EXAMPLE_IDEA =
  "I want to build an app that helps people determine whether their plants are healthy using their smartphone camera.";
