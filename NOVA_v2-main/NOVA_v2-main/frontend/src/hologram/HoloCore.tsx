import { useEffect, useRef, useState } from "react";
import { type AssistantState, STATE_LABELS } from "../assistant/machine";
import { useReducedMotion, useResolvedTheme } from "../store/prefs";

export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/** SVG core used when WebGL is unavailable (or fails). Same states, no 3D. */
export function FallbackCore({ state }: { state: AssistantState }) {
  const ticks = Array.from({ length: 72 }, (_, i) => {
    const t = (i / 72) * Math.PI * 2;
    const len = i % 6 === 0 ? 7 : 3;
    return <line key={i} x1={100 + Math.cos(t) * 62} y1={100 + Math.sin(t) * 62} x2={100 + Math.cos(t) * (62 + len)} y2={100 + Math.sin(t) * (62 + len)} />;
  });
  return (
    <svg className={`fallback-core holo-${state}`} viewBox="0 0 200 200" role="img" aria-label={`NOVA: ${STATE_LABELS[state]}`}>
      <circle cx="100" cy="100" r="34" className="fc-glow" />
      <circle cx="100" cy="100" r="22" className="fc-core" />
      <circle cx="100" cy="100" r="58" className="fc-ring" />
      <g className="fc-ticks">{ticks}</g>
      <path d="M100 30 A70 70 0 0 1 170 100" className="fc-arc fc-arc-a" />
      <path d="M30 100 A70 70 0 0 0 100 170" className="fc-arc fc-arc-b" />
      <circle cx="100" cy="100" r="88" className="fc-orbit" />
    </svg>
  );
}

interface Props {
  state: AssistantState;
  levelSource: (() => number) | null;
  pulse: number;
}

/** The central holographic core: WebGL (three.js, lazy-loaded) with an SVG fallback. */
export function HoloCore({ state, levelSource, pulse }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<import("./CoreScene").CoreScene | null>(null);
  const [mode, setMode] = useState<"loading" | "webgl" | "fallback">(() => (hasWebGL() ? "loading" : "fallback"));
  const theme = useResolvedTheme();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (mode !== "loading") return;
    let disposed = false;
    let ro: ResizeObserver | null = null;
    import("./CoreScene")
      .then(({ CoreScene }) => {
        if (disposed || !canvasRef.current || !wrapRef.current) return;
        const lowPower = (navigator.hardwareConcurrency ?? 4) <= 4 || window.innerWidth < 700;
        const scene = new CoreScene(canvasRef.current, { reducedMotion, lowPower, theme });
        sceneRef.current = scene;
        const fit = () => scene.resize(wrapRef.current!.clientWidth, wrapRef.current!.clientHeight);
        ro = new ResizeObserver(fit);
        ro.observe(wrapRef.current);
        fit();
        setMode("webgl");
      })
      .catch(() => !disposed && setMode("fallback"));
    return () => {
      disposed = true;
      ro?.disconnect();
      sceneRef.current?.dispose();
      sceneRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => sceneRef.current?.setState(state), [state, mode]);
  useEffect(() => sceneRef.current?.setTheme(theme), [theme, mode]);
  useEffect(() => sceneRef.current?.setReducedMotion(reducedMotion), [reducedMotion, mode]);
  useEffect(() => sceneRef.current?.setLevelSource(levelSource), [levelSource, mode]);
  useEffect(() => {
    if (pulse) sceneRef.current?.pulse();
  }, [pulse]);

  return (
    <div className={`holo-core holo-${state}`} ref={wrapRef}>
      {mode === "fallback" ? (
        <FallbackCore state={state} />
      ) : (
        <canvas ref={canvasRef} role="img" aria-label={`NOVA: ${STATE_LABELS[state]}`} />
      )}
    </div>
  );
}
