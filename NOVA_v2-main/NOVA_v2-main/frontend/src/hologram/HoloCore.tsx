import { memo, type RefObject, useEffect, useRef, useState } from "react";
import { type AssistantState, STATE_LABELS } from "../assistant/machine";
import { useReducedMotion, useResolvedTheme } from "../store/prefs";
import type { HolographicScene } from "./holographic/HolographicScene";

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

interface Focus { left: number; top: number; width: number; height: number }

interface Props {
  state: AssistantState;
  levelSource: (() => number) | null;
  pulse: number;
  /** The element the hologram centres on and fills (it sits behind the page; the rest is environment). */
  focusRef?: RefObject<HTMLElement | null>;
}

/**
 * NOVA's holographic presence (Holographic Core 2.0): the energy core that becomes an avatar, drawn by three.js
 * behind the command center, with an SVG fallback. Driven only by the real assistant state, the real microphone
 * level and real speech word boundaries.
 */
// Memoised: the command center re-renders on every streamed token; the hologram only cares about its own props.
export const HoloCore = memo(function HoloCore({ state, levelSource, pulse, focusRef }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<HolographicScene | null>(null);
  const [mode, setMode] = useState<"loading" | "webgl" | "fallback">(() => (hasWebGL() ? "loading" : "fallback"));
  const [focus, setFocus] = useState<Focus | null>(null);
  const theme = useResolvedTheme();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (mode !== "loading") return;
    let disposed = false;
    let ro: ResizeObserver | null = null;
    Promise.all([import("./holographic/HolographicScene"), import("./holographic/PortraitAvatar")])
      .then(([{ HolographicScene }, { LAB_PORTRAIT }]) => {
        if (disposed || !canvasRef.current || !wrapRef.current) return;
        const lowPower = (navigator.hardwareConcurrency ?? 4) <= 4 || window.innerWidth < 700;
        // The portrait form is used when its image is available on this machine (frontend/hologram-assets/,
        // never distributed); otherwise the scene keeps the line-art figure.
        const scene = new HolographicScene(canvasRef.current, { reducedMotion, lowPower, theme, backdrop: true, portrait: LAB_PORTRAIT });
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

  // Follow the focus element (layout changes, resizing, scrolling of the stage).
  useEffect(() => {
    const wrap = wrapRef.current;
    const target = focusRef?.current;
    if (!wrap || !target || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const a = wrap.getBoundingClientRect();
      const b = target.getBoundingClientRect();
      const f = { left: b.left - a.left, top: b.top - a.top, width: b.width, height: b.height };
      setFocus((p) => (p && Math.abs(p.left - f.left) + Math.abs(p.top - f.top) + Math.abs(p.width - f.width) + Math.abs(p.height - f.height) < 1 ? p : f));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(wrap);
    ro.observe(target);
    const scroller = target.closest(".stage");
    scroller?.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      scroller?.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, [focusRef, mode]);

  useEffect(() => {
    if (focus) sceneRef.current?.setFocus(focus.left, focus.top, focus.width, focus.height);
  }, [focus, mode]);
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
        <div className="holo-fallback" style={focus ? { left: focus.left, top: focus.top, width: focus.width, height: focus.height } : undefined}>
          <FallbackCore state={state} />
        </div>
      ) : (
        <canvas ref={canvasRef} role="img" aria-label={`NOVA: ${STATE_LABELS[state]}`} />
      )}
    </div>
  );
});
