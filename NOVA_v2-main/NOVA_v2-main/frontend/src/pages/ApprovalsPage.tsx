import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ActionRequest } from "../api/types";
import { ApprovalDialog, describeAction } from "../components/Approval";
import { Empty, Notice, formatTime } from "../components/ui";
import { useLoad } from "../hooks/useLoad";

export function ApprovalsPage() {
  const [status, setStatus] = useState("pending");
  const { data, error, reload } = useLoad(() => api.actions.list(status || undefined), [status]);
  const [open, setOpen] = useState<ActionRequest | null>(null);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Approvals</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Show">
          <option value="pending">Waiting for you</option>
          <option value="">All requests</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired">Expired</option>
        </select>
      </div>
      <p className="muted">Consequential actions — running commands, deleting files and safety-critical device actions such as unlocking a door — wait here until you approve them. An approval runs that exact action once. Requests expire after 10 minutes, and voice can never approve them.</p>
      {error && <Notice>{error}</Notice>}
      {data?.length === 0 ? (
        <Empty title={status === "pending" ? "Nothing needs your approval" : "No requests"} />
      ) : (
        <table className="table">
          <thead><tr><th>Action</th><th>Reason</th><th>Status</th><th>Requested</th><th>Project</th></tr></thead>
          <tbody>
            {data?.map((a) => (
              <tr key={a.id}>
                <td><button type="button" className="link-btn" onClick={() => setOpen(a)}>{describeAction(a).what.replace(/`/g, "")}</button></td>
                <td className="small">{a.reason || <span className="muted">—</span>}</td>
                <td><span className={`badge badge-${a.status}`}>{a.status}</span></td>
                <td className="small muted">{formatTime(a.created_at)}</td>
                <td className="small">{a.project_id !== null ? <Link to={`/projects/${a.project_id}/tasks`}>Open</Link> : <span className="muted">Device</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <ApprovalDialog action={open} onClose={() => { setOpen(null); void reload(); }} onDecided={reload} />}
    </div>
  );
}
