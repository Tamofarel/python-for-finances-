import { useEffect, useRef } from "react";
import { useResolvedTheme } from "../store/prefs";

interface Props {
  active: boolean; // hidden (but keeping its space) unless NOVA is listening or speaking
  samples: (() => Uint8Array | null) | null; // real microphone time-domain data while listening
  pulse: number; // increments on real speech-synthesis word boundaries
  speaking: boolean;
  reducedMotion?: boolean;
}

/**
 * Voice waveform. While listening it draws the actual microphone signal. While NOVA speaks, the browser does
 * not expose synthesized audio, so it shows one bar per spoken word boundary instead. Otherwise it is flat.
 */
export function Waveform({ active, samples, pulse, speaking, reducedMotion }: Props) {
  const theme = useResolvedTheme();
  const ref = useRef<HTMLCanvasElement>(null);
  const bars = useRef<number[]>([]);

  useEffect(() => {
    if (speaking && pulse) bars.current = [...bars.current.slice(-47), 0.7]; // one bar per real word boundary; height carries no meaning
  }, [pulse, speaking]);

  useEffect(() => {
    const canvas = ref.current;
    const g = canvas?.getContext("2d");
    if (!canvas || !g) return;
    let frame = 0;
    // Theme colours come from the CSS tokens, so the waveform follows light and dark themes.
    const css = getComputedStyle(canvas);
    const color = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const signal = color("--signal", "#6fd3ea");
    const aura = color("--aura", "#a59ff7");
    const faint = color("--seam-strong", "#3c4d72");
    const draw = () => {
      const w = (canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1));
      const h = (canvas.height = canvas.clientHeight * (window.devicePixelRatio || 1));
      g.clearRect(0, 0, w, h);
      const data = samples?.();
      g.lineWidth = Math.max(1, h / 40);
      if (data) {
        g.strokeStyle = signal;
        g.beginPath();
        for (let i = 0; i < data.length; i += 4) {
          const x = (i / data.length) * w;
          const y = h / 2 + ((data[i]! - 128) / 128) * (h / 2) * 1.6;
          if (i === 0) g.moveTo(x, y);
          else g.lineTo(x, y);
        }
        g.stroke();
      } else if (speaking) {
        g.fillStyle = aura;
        const n = 48;
        const bw = w / n;
        bars.current = bars.current.map((b) => b * 0.94);
        bars.current.forEach((b, i) => {
          const bh = Math.max(2, b * h);
          g.fillRect((n - bars.current.length + i) * bw + bw * 0.25, (h - bh) / 2, bw * 0.5, bh);
        });
      } else {
        g.strokeStyle = faint;
        g.beginPath();
        g.moveTo(0, h / 2);
        g.lineTo(w, h / 2);
        g.stroke();
      }
      if (!reducedMotion && (data || speaking)) frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [samples, speaking, reducedMotion, pulse, theme]);

  return <canvas ref={ref} className={`waveform ${active ? "" : "idle"}`} aria-hidden="true" />;
}
