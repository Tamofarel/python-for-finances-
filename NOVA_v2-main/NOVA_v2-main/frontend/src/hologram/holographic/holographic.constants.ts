// Colour tokens and the mapping from the real assistant state to what the hologram shows.
// No three.js import here: this file is safe to load (and test) without WebGL.

import type { AssistantState } from "../../assistant/machine";
import type { HoloTheme, HolographicState, Hue, VisualProfile } from "./holographic.types";

// On the light theme the hologram is drawn with normal blending and deeper inks: additive glow vanishes on white.
export const PALETTES: Record<HoloTheme, Record<Hue | "hi", string>> = {
  dark: { blue: "#4a63ff", violet: "#8a6cff", cyan: "#6fd3ea", warn: "#f2c46b", danger: "#f28b8b", muted: "#56627d", hi: "#f4f7ff" },
  light: { blue: "#2f5bd8", violet: "#5b50c9", cyan: "#0b7d98", warn: "#a86b00", danger: "#b4362a", muted: "#9aa3b3", hi: "#16245e" },
};

/** Which visual family each real assistant state belongs to. */
export const VISUAL_STATE: Record<AssistantState, HolographicState> = {
  standby: "idle",
  awaiting_user: "idle",
  awaiting_approval: "idle",
  wake_detected: "listening",
  listening: "listening",
  transcribing: "thinking",
  understanding: "thinking",
  thinking: "thinking",
  responding: "responding",
  speaking: "speaking",
  researching: "executing",
  designing: "executing",
  executing: "executing",
  interrupted: "interrupted",
  cancelled: "interrupted",
  error: "error",
};

// Calm by default: motion and the avatar only appear when NOVA is actually doing something.
const FAMILY: Record<HolographicState, VisualProfile> = {
  idle: { a: "blue", b: "violet", energy: 0.45, spin: 0.04, expand: 1, morph: 0, flow: 0.15, scan: 0 },
  listening: { a: "blue", b: "cyan", energy: 0.8, spin: 0.08, expand: 1.06, morph: 0, flow: 0.3, scan: 0 },
  thinking: { a: "violet", b: "blue", energy: 0.62, spin: 0.26, expand: 1.22, morph: 0.46, flow: 0.9, scan: 1 },
  responding: { a: "blue", b: "violet", energy: 0.72, spin: 0.12, expand: 1, morph: 0.88, flow: 0.45, scan: 0.15 },
  speaking: { a: "violet", b: "blue", energy: 0.85, spin: 0.1, expand: 1, morph: 1, flow: 0.3, scan: 0 },
  executing: { a: "blue", b: "cyan", energy: 0.62, spin: 0.2, expand: 1.05, morph: 0.7, flow: 0.7, scan: 0.5 },
  error: { a: "danger", b: "muted", energy: 0.34, spin: 0.012, expand: 0.94, morph: 0, flow: 0.04, scan: 0 },
  interrupted: { a: "muted", b: "muted", energy: 0.18, spin: 0, expand: 0.94, morph: 0, flow: 0, scan: 0 },
};

const OVERRIDES: Partial<Record<AssistantState, Partial<VisualProfile>>> = {
  wake_detected: { energy: 0.88, expand: 1.12 },
  transcribing: { morph: 0.12, energy: 0.55, spin: 0.14, expand: 1.1, scan: 0.3 },
  understanding: { morph: 0.28, expand: 1.16 }, // shown the instant a request is sent, before the model answers
  awaiting_user: { energy: 0.42 },
  awaiting_approval: { a: "warn", energy: 0.46 },
  researching: { morph: 0.5, scan: 1 },
  designing: { a: "violet", b: "blue", morph: 0.5, scan: 0.2 },
};

export const PROFILES = Object.fromEntries(
  (Object.keys(VISUAL_STATE) as AssistantState[]).map((s) => [s, { ...FAMILY[VISUAL_STATE[s]], ...OVERRIDES[s] }]),
) as Record<AssistantState, VisualProfile>;

/** Particle budget per quality tier. */
export const PARTICLES = { full: 7000, low: 2500 } as const;
/** Device pixel ratio caps per quality tier. */
export const MAX_DPR = { full: 1.75, low: 1.25 } as const;
/** Where the energy core sits once the avatar has formed (the avatar's chest), in scene units. */
export const CHEST_Y = -0.7;
