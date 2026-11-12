// Safe Markdown rendering: react-markdown never renders raw HTML, and unsafe URLs (javascript:, data:)
// are stripped by its default urlTransform. Mermaid runs in "strict" security mode and is loaded lazily.

import { Check, Copy } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { useResolvedTheme } from "../store/prefs";

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost btn-xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard unavailable (insecure context) */
        }
      }}
      aria-label={done ? "Copied" : label}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
      <span>{done ? "Copied" : label}</span>
    </button>
  );
}

function MermaidBlock({ code }: { code: string }) {
  const theme = useResolvedTheme();
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: MERMAID_THEMES[theme],
        });
        const out = await mermaid.render(`m${id}`, code);
        if (alive) setSvg(out.svg);
      })
      .catch((e: Error) => alive && setErr(e.message || "Invalid diagram"));
    return () => {
      alive = false;
    };
  }, [code, id, theme]);
  if (err) {
    return (
      <div className="code-block">
        <div className="code-head"><span>Mermaid (could not render: {err})</span><CopyButton text={code} /></div>
        <pre><code>{code}</code></pre>
      </div>
    );
  }
  // Mermaid output is generated with securityLevel "strict" (labels sanitized, no click handlers).
  return svg ? <div className="mermaid-view" dangerouslySetInnerHTML={{ __html: svg }} /> : <div className="muted small">Rendering diagram…</div>;
}

const FONT = "Instrument Sans Variable, system-ui, sans-serif";
const MERMAID_THEMES = {
  dark: {
    darkMode: true, background: "#142035", primaryColor: "#1a2843", primaryTextColor: "#e8eef7", primaryBorderColor: "#6fd3ea",
    secondaryColor: "#213152", tertiaryColor: "#142035", lineColor: "#9eabc4", textColor: "#e8eef7", fontFamily: FONT,
  },
  light: {
    darkMode: false, background: "#ffffff", primaryColor: "#eef3f8", primaryTextColor: "#172235", primaryBorderColor: "#0a7590",
    secondaryColor: "#f0f2f5", tertiaryColor: "#ffffff", lineColor: "#536077", textColor: "#172235", fontFamily: FONT,
  },
} as const;

export function Markdown({ children, diagrams = true }: { children: string; diagrams?: boolean }) {
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={{
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">{c}</a>
          ),
          pre: ({ children: c }) => {
            const child = Array.isArray(c) ? c[0] : c;
            const className = (child as { props?: { className?: string } })?.props?.className ?? "";
            const lang = /language-([\w-]+)/.exec(className)?.[1] ?? "";
            const code = textOf(c).replace(/\n$/, "");
            if (lang === "mermaid" && diagrams) return <MermaidBlock code={code} />;
            return (
              <div className="code-block">
                <div className="code-head">
                  <span>{lang || "text"}</span>
                  <CopyButton text={code} />
                </div>
                <pre>{c}</pre>
              </div>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
