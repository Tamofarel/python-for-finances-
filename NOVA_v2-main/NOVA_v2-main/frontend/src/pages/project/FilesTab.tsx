import { ChevronRight, File, FilePlus, Folder, FolderInput, FolderPlus, ScrollText, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { ActionRequest, Artifact, ChangeProposal, FileContent, FileEntry, Project } from "../../api/types";
import { ApprovalDialog } from "../../components/Approval";
import { Empty, Field, Modal, Notice, Spinner, formatBytes, formatTime } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { ChangeReview } from "./ChangeReview";

export function FilesTab({ project }: { project: Project }) {
  const [dir, setDir] = useState("");
  const listing = useLoad(() => api.files.list(project.id, dir), [project.id, dir]);
  const [file, setFile] = useState<FileContent | null>(null);
  const [text, setText] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<ChangeProposal | null>(null);
  const [creating, setCreating] = useState<"file" | "dir" | null>(null);
  const [newName, setNewName] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteAction, setDeleteAction] = useState<ActionRequest | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [moveTo, setMoveTo] = useState("");
  const [summary, setSummary] = useState<Artifact | null>(null);

  useEffect(() => {
    setFile(null);
    setProposal(null);
  }, [dir]);

  async function openFile(entry: FileEntry) {
    if (entry.type === "dir") {
      setDir(entry.path);
      return;
    }
    setFileError(null);
    setProposal(null);
    try {
      const f = await api.files.read(project.id, entry.path);
      setFile(f);
      setText(f.content);
    } catch (e) {
      setFile(null);
      setFileError((e as Error).message);
    }
  }

  async function reloadFile() {
    if (!file) return;
    const f = await api.files.read(project.id, file.path);
    setFile(f);
    setText(f.content);
  }

  async function act<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  const modified = file ? listing.data?.find((e) => e.path === file.path)?.modified : undefined;
  const crumbs = dir ? dir.split("/") : [];
  const dirty = file !== null && text !== file.content;

  return (
    <div className="files">
      <aside className="explorer" aria-label="Workspace files">
        <div className="row explorer-head">
          <nav className="crumbs" aria-label="Folder">
            <button type="button" className="link-btn" onClick={() => setDir("")}>workspace</button>
            {crumbs.map((c, i) => (
              <span key={i}>
                <ChevronRight size={12} aria-hidden />
                <button type="button" className="link-btn" onClick={() => setDir(crumbs.slice(0, i + 1).join("/"))}>{c}</button>
              </span>
            ))}
          </nav>
          <span className="spacer" />
          <button type="button" className="btn btn-ghost btn-icon" aria-label="New file" onClick={() => { setCreating("file"); setNewName(""); }}><FilePlus size={15} /></button>
          <button type="button" className="btn btn-ghost btn-icon" aria-label="New folder" onClick={() => { setCreating("dir"); setNewName(""); }}><FolderPlus size={15} /></button>
        </div>
        {listing.error && <Notice>{listing.error}</Notice>}
        {listing.loading && !listing.data && <Spinner />}
        {listing.data?.length === 0 && <p className="muted small pad">This folder is empty. Sensitive files such as .env or keys are hidden by policy.</p>}
        <ul className="file-list">
          {listing.data?.map((e) => (
            <li key={e.path}>
              <button type="button" className={file?.path === e.path ? "active" : ""} onClick={() => void openFile(e)}>
                {e.type === "dir" ? <Folder size={15} aria-hidden /> : <File size={15} aria-hidden />}
                <span className="file-name">{e.name}</span>
                {e.type === "file" && <span className="small muted">{formatBytes(e.size)}</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className="explorer-foot">
          <button type="button" className="btn btn-sm btn-block" disabled={busy}
            onClick={() => act(async () => setSummary(await api.projects.summarize(project.id)))}>
            <ScrollText size={14} aria-hidden /> {busy ? "Working…" : "Summarize project with NOVA"}
          </button>
          {summary && <p className="small">Summary saved. <Link to="../design">Open it in Design</Link></p>}
        </div>
      </aside>
      <section className="editor" aria-label="Editor">
        {error && <Notice onDismiss={() => setError(null)}>{error}</Notice>}
        {fileError && <Notice>{fileError}</Notice>}
        {!file && !fileError && <Empty title="Pick a file to view or edit">Edits are never written directly: you review a diff first, then apply it.</Empty>}
        {file && (
          <>
            <div className="row editor-head">
              <code>{file.path}</code>
              <span className="small muted">{formatBytes(file.size)}{modified ? `, modified ${formatTime(modified)}` : ""}</span>
              <span className="spacer" />
              <button type="button" className="btn btn-sm" onClick={() => setAiOpen(true)}><Sparkles size={14} aria-hidden /> Ask NOVA to change it</button>
              <button type="button" className="btn btn-ghost btn-icon" aria-label={`Move or rename ${file.path}`} title="Move or rename (needs your approval)"
                onClick={() => { setMoving(file.path); setMoveTo(file.path); }}>
                <FolderInput size={15} />
              </button>
              <button type="button" className="btn btn-ghost btn-icon" aria-label={`Delete ${file.path}`}
                onClick={() => act(async () => setDeleteAction(await api.actions.requestDelete(project.id, file.path, "Delete from file explorer")))}>
                <Trash2 size={15} />
              </button>
            </div>
            {proposal ? (
              <>
                <ChangeReview change={proposal} onUpdated={async (c) => {
                  setProposal(c);
                  if (c.status === "applied") {
                    await reloadFile();
                    await listing.reload();
                  }
                }} />
                {proposal.status !== "pending" && (
                  <button type="button" className="btn btn-sm" onClick={() => setProposal(null)}>Back to editor</button>
                )}
              </>
            ) : (
              <>
                <textarea className="code-editor" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label={`Contents of ${file.path}`} />
                <div className="row">
                  <button type="button" className="btn btn-primary" disabled={!dirty || busy}
                    onClick={() => act(async () => setProposal(await api.changes.propose(project.id, { path: file.path, content: text, base_sha256: file.sha256, summary: "Manual edit" })))}>
                    Review changes
                  </button>
                  {dirty && <button type="button" className="btn" onClick={() => setText(file.content)}>Discard edits</button>}
                  <span className="small muted">{dirty ? "Unsaved edits" : "No edits"}</span>
                </div>
              </>
            )}
          </>
        )}
      </section>

      {creating && (
        <Modal title={creating === "file" ? "New file" : "New folder"} onClose={() => setCreating(null)} footer={
          <>
            <button type="button" className="btn" onClick={() => setCreating(null)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!newName.trim() || busy} onClick={async () => {
              const path = dir ? `${dir}/${newName.trim()}` : newName.trim();
              const entry = await act(() => api.files.create(project.id, path, creating));
              if (entry) {
                setCreating(null);
                await listing.reload();
                if (entry.type === "file") await openFile(entry);
              }
            }}>Create</button>
          </>
        }>
          <Field label="Name" hint={`Created in ${dir || "the workspace root"}. Paths outside the workspace are rejected.`}>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={creating === "file" ? "notes.md" : "docs"} />
          </Field>
          {error && <Notice>{error}</Notice>}
        </Modal>
      )}

      {aiOpen && file && (
        <Modal title={`Ask NOVA to change ${file.path}`} onClose={() => setAiOpen(false)} footer={
          <>
            <button type="button" className="btn" onClick={() => setAiOpen(false)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={instruction.trim().length < 3 || busy} onClick={async () => {
              const cp = await act(() => api.changes.proposeAI(project.id, file.path, instruction.trim()));
              if (cp) {
                setProposal(cp);
                setAiOpen(false);
                setInstruction("");
              }
            }}>{busy ? "NOVA is writing a proposal…" : "Propose a change"}</button>
          </>
        }>
          <Field label="What should change?" hint="NOVA returns a proposal with a diff. Nothing is written until you apply it.">
            <textarea rows={4} value={instruction} onChange={(e) => setInstruction(e.target.value)} />
          </Field>
          {error && <Notice>{error}</Notice>}
        </Modal>
      )}

      {moving && (
        <Modal title="Move or rename" onClose={() => setMoving(null)} footer={
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setMoving(null)}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={busy || !moveTo.trim() || moveTo.trim() === moving}
              onClick={() => act(async () => {
                const a = await api.actions.requestMove(project.id, moving, moveTo.trim(), "Move from file explorer");
                setMoving(null);
                setDeleteAction(a);
              })}>Review the move</button>
          </>
        }>
          <div className="stack">
            <p className="small muted">NOVA asks for your approval before moving anything. An existing file is never overwritten.</p>
            <Field label="New path inside the workspace" hint={`Currently ${moving}`}>
              <input value={moveTo} onChange={(e) => setMoveTo(e.target.value)} autoFocus />
            </Field>
            {error && <Notice>{error}</Notice>}
          </div>
        </Modal>
      )}

      {deleteAction && (
        <ApprovalDialog action={deleteAction} onClose={() => setDeleteAction(null)} onDecided={async () => {
          await listing.reload();
          setFile(null);
        }} />
      )}
    </div>
  );
}
