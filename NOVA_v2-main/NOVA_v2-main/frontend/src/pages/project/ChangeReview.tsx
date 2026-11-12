import { useState } from "react";
import { api } from "../../api/client";
import type { ChangeProposal } from "../../api/types";
import { DiffView } from "../../components/DiffView";
import { Notice, formatTime } from "../../components/ui";

/** Shows a proposal's diff and lets the owner apply (bound to the content hash) or reject it. */
export function ChangeReview({ change, onUpdated }: { change: ChangeProposal; onUpdated: (c: ChangeProposal) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<ChangeProposal>) {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="change">
      <header className="change-head">
        <div>
          <strong><code>{change.path}</code></strong>
          <span className="small muted">
            {change.operation === "create" ? "New file" : "Edit"} proposed by {change.source === "ai" ? "NOVA" : "you"}, {formatTime(change.created_at)}
          </span>
        </div>
        <span className={`badge badge-${change.status}`}>{change.status}</span>
      </header>
      {change.summary && <p className="change-summary">{change.summary}</p>}
      <DiffView diff={change.diff} />
      {error && <Notice>{error}</Notice>}
      {change.status === "pending" && (
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={busy}
            onClick={() => run(() => api.changes.apply(change.project_id, change.id, change.proposed_sha256))}>
            Apply change
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.changes.reject(change.project_id, change.id))}>
            Discard
          </button>
          <span className="small muted">Applying writes exactly this diff. The previous version is kept so you can revert.</span>
        </div>
      )}
      {change.status === "applied" && (
        <div className="row">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => run(() => api.changes.revert(change.project_id, change.id))}>
            Revert to previous version
          </button>
        </div>
      )}
      {change.status === "stale" && <Notice kind="warn">The file changed after this proposal was made, so it was not applied. Create a new proposal from the current file.</Notice>}
    </div>
  );
}
