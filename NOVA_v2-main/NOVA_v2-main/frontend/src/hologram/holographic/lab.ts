// Development-only lab for the Holographic Core 2.0 (open /hologram-lab.html on the Vite dev server).
// It drives the scene by hand so the visuals can be judged without a backend. It is not part of the production
// build, and its "test level" slider is a lab input only: in the app the level always comes from the microphone.
//
// URL parameters: ?state=thinking&theme=light&motion=reduce&low=1&level=0.6&bare=1&nobg=1&portrait=1 (needs frontend/hologram-assets/portrait.jpg)

import "../../styles.css";
import { type AssistantState, STATE_LABELS } from "../../assistant/machine";
import { HolographicScene } from "./HolographicScene";
import { LAB_PORTRAIT } from "./PortraitAvatar";
import { VISUAL_STATE } from "./holographic.constants";

const q = new URLSearchParams(location.search);
const canvas = document.getElementById("c") as HTMLCanvasElement;
const stage = document.getElementById("stage")!;
const panel = document.getElementById("panel")!;
const label = document.getElementById("label")!;
const statsEl = document.getElementById("stats")!;

let theme = q.get("theme") === "light" ? "light" as const : "dark" as const;
let reduced = q.get("motion") === "reduce";
let level = Number(q.get("level") ?? 0);
document.documentElement.dataset.theme = theme;
document.body.classList.toggle("bare", q.has("bare"));

const scene = new HolographicScene(canvas, { reducedMotion: reduced, lowPower: q.has("low"), theme, backdrop: !q.has("nobg"),
  portrait: q.has("portrait") ? LAB_PORTRAIT : undefined });
scene.setLevelSource(() => level);
new ResizeObserver(() => scene.resize(stage.clientWidth, stage.clientHeight)).observe(stage);
scene.resize(stage.clientWidth, stage.clientHeight);

const buttons = new Map<AssistantState, HTMLButtonElement>();
function setState(s: AssistantState): void {
  scene.setState(s);
  label.textContent = `${STATE_LABELS[s]} · ${VISUAL_STATE[s]}`;
  buttons.forEach((b, key) => b.setAttribute("aria-pressed", String(key === s)));
}

function button(text: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.onclick = onClick;
  panel.append(b);
  return b;
}

for (const s of Object.keys(VISUAL_STATE) as AssistantState[]) buttons.set(s, button(s, () => setState(s)));
button("theme", () => {
  theme = theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  scene.setTheme(theme);
});
button("reduced motion", () => scene.setReducedMotion((reduced = !reduced)));
button("word pulse", () => scene.pulse());
const slider = document.createElement("label");
slider.innerHTML = `test level (lab only) <input type="range" min="0" max="1" step="0.01" value="${level}">`;
slider.querySelector("input")!.oninput = (e) => (level = Number((e.target as HTMLInputElement).value));
panel.append(slider);

setState((q.get("state") as AssistantState) in VISUAL_STATE ? (q.get("state") as AssistantState) : "standby");
setInterval(() => {
  const s = scene.stats();
  statsEl.textContent = `${s.fps} fps · ${s.frameMs} ms avg · ${s.worstMs} ms worst\n${s.drawCalls} draw calls · ${s.particles} particles · dpr ${s.pixelRatio}${s.degraded ? " (degraded)" : ""}`;
}, 1000);

// For automated checks (screenshots, timing).
Object.assign(window, { holoLab: { scene, setState, setLevel: (v: number) => (level = v) } });
