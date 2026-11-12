import { Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Conversation } from "../api/types";
import { Empty, Modal, Notice, formatTime } from "../components/ui";
import { useChat } from "../store/chat";

export function HistoryPage() {
  const { conversations, query, setQuery, loadList, listError, rename, remove } = useChat();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [title, setTitle] = useState("");
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => void loadList(), 200);
    return () => clearTimeout(t);
  }, [query, loadList]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Conversation history</h1>
        <input type="search" className="page-search" placeholder="Search titles and messages" aria-label="Search conversations"
          value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {(listError || error) && <Notice>{listError || error}</Notice>}
      {conversations.length === 0 ? (
        <Empty title={query ? "No matches" : "No conversations yet"}>
          {query ? "Try a different word. Search covers titles and message text." : "Start one from the Assistant screen."}
        </Empty>
      ) : (
        <table className="table">
          <thead><tr><th>Title</th><th>Model</th><th>Updated</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {conversations.map((c) => (
              <tr key={c.id}>
                <td><button type="button" className="link-btn" onClick={() => navigate(`/c/${c.id}`)}>{c.title}</button></td>
                <td className="muted small">{c.model ?? "—"}</td>
                <td className="muted small">{formatTime(c.updated_at)}</td>
                <td className="row-actions">
                  <button type="button" className="btn btn-ghost btn-icon" aria-label={`Rename ${c.title}`} onClick={() => { setRenaming(c); setTitle(c.title); }}><Pencil size={15} /></button>
                  <button type="button" className="btn btn-ghost btn-icon" aria-label={`Delete ${c.title}`} onClick={() => setDeleting(c)}><Trash2 size={15} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {renaming && (
        <Modal title="Rename conversation" onClose={() => setRenaming(null)} footer={
          <>
            <button type="button" className="btn" onClick={() => setRenaming(null)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!title.trim()} onClick={async () => {
              try { await rename(renaming.id, title.trim()); setRenaming(null); } catch (e) { setError((e as Error).message); }
            }}>Save name</button>
          </>
        }>
          <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Conversation title" maxLength={200} />
        </Modal>
      )}
      {deleting && (
        <Modal title="Delete conversation?" onClose={() => setDeleting(null)} footer={
          <>
            <button type="button" className="btn" onClick={() => setDeleting(null)}>Keep it</button>
            <button type="button" className="btn btn-danger" onClick={async () => {
              try { await remove(deleting.id); setDeleting(null); } catch (e) { setError((e as Error).message); }
            }}>Delete conversation</button>
          </>
        }>
          <p>“{deleting.title}” and all its messages will be permanently deleted from NOVA's database.</p>
        </Modal>
      )}
    </div>
  );
}
