import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ChangeProposal, Project } from "../../api/types";
import { Empty, Notice, Spinner, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { ChangeReview } from "./ChangeReview";

const FILTERS = [
  ["pending", "Waiting for review"],
  ["", "All"],
  ["applied", "Applied"],
  ["rejected", "Discarded"],
] as const;

export function ChangesTab({ project, onPendingCount }: { project: Project; onPendingCount: (n: number) => void }) {
  const [filter, setFilter] = useState<string>("pending");
  const { data, error, loading, setData } = useLoad(() => api.changes.list(project.id, filter || undefined), [project.id, filter]);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    if (filter === "pending" && data) onPendingCount(data.length);
  }, [data, filter, onPendingCount]);

  useEffect(() => {
    if (data && !data.some((c) => c.id === selected)) setSelected(data[0]?.id ?? null);
  }, [data, selected]);

  const current = data?.find((c) => c.id === selected) ?? null;

  function updated(c: ChangeProposal) {
    setData((list) => (list ? list.map((x) => (x.id === c.id ? c : x)) : list));
  }

  return (
    <div className="split">
      <aside className="split-list" aria-label="Change proposals">
        <div className="segmented" role="group" aria-label="Filter changes">
          {FILTERS.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={filter === value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
          ))}
        </div>
        {error && <Notice>{error}</Notice>}
        {loading && !data && <Spinner />}
        {data?.length === 0 && <p className="muted small pad">No changes here. Edit a file or ask NOVA for a change from the Files tab.</p>}
        <ul className="plain-list selectable">
          {data?.map((c) => (
            <li key={c.id}>
              <button type="button" className={c.id === selected ? "active" : ""} onClick={() => setSelected(c.id)}>
                <code>{c.path}</code>
                <span className="small muted">{c.source === "ai" ? "NOVA" : "You"}, {formatTime(c.created_at)} <span className={`badge badge-${c.status}`}>{c.status}</span></span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="split-main">
        {current ? <ChangeReview change={current} onUpdated={updated} /> : <Empty title="Nothing selected">Pick a proposal to review its diff.</Empty>}
      </section>
    </div>
  );
}
