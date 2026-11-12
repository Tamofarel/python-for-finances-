import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { Notice, Spinner } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";

const STATUS = { available: "Available", limited: "Limited", unavailable: "Not available" } as const;

/** What NOVA can do right now, from live checks of models, search and speech engines. */
export function CapabilitiesList() {
  const caps = useLoad(() => api.capabilities(), []);
  return (
    <div className="stack settings-section">
      <p className="small muted">Checked just now against your configured models, search provider and speech engines. Nothing here is assumed from configuration alone.</p>
      {caps.error && <Notice>{caps.error}</Notice>}
      {!caps.data && !caps.error && <p className="muted"><Spinner /> Checking…</p>}
      <ul className="caps">
        {caps.data?.map((c) => (
          <li key={c.id} className={`cap cap-${c.status}`}>
            <div className="cap-head">
              <strong>{c.label}</strong>
              <span className={`badge badge-cap-${c.status}`}>{STATUS[c.status]}</span>
            </div>
            <p className="small">{c.description}</p>
            {c.reason && <p className="small cap-reason">{c.reason}</p>}
            <p className="small muted">How: {c.implementation}</p>
            <Link className="small" to={c.entry}>Open</Link>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-sm self-start" onClick={() => void caps.reload()}>Check again</button>
    </div>
  );
}
