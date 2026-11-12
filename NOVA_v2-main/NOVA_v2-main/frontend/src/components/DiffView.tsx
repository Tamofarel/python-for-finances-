/** Renders a unified diff produced by the backend (difflib), with line numbers. */
export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="muted">No textual differences.</p>;
  let oldNo = 0;
  let newNo = 0;
  const rows = diff.replace(/\n$/, "").split("\n").map((line, i) => {
    if (line.startsWith("---") || line.startsWith("+++")) {
      return <tr key={i} className="diff-file"><td colSpan={3}>{line}</td></tr>;
    }
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      return <tr key={i} className="diff-hunk"><td colSpan={3}>{line}</td></tr>;
    }
    if (line.startsWith("+")) {
      return <tr key={i} className="diff-add"><td className="ln" /><td className="ln">{newNo++}</td><td><span className="sr-only">added: </span>{line}</td></tr>;
    }
    if (line.startsWith("-")) {
      return <tr key={i} className="diff-del"><td className="ln">{oldNo++}</td><td className="ln" /><td><span className="sr-only">removed: </span>{line}</td></tr>;
    }
    return <tr key={i}><td className="ln">{oldNo++}</td><td className="ln">{newNo++}</td><td>{line}</td></tr>;
  });
  return (
    <div className="diff" role="region" aria-label="Diff">
      <table><tbody>{rows}</tbody></table>
    </div>
  );
}
