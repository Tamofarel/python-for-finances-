// Types of the Holographic Core 2.0 visual system.
// The application state is NOT redefined here: `AssistantState` (assistant/machine.ts) stays the single source of
// truth. `HolographicState` is only the visual family a state is drawn with.

import type { Color, Object3D } from "three";

export type HolographicState =
  | "idle"
  | "listening"
  | "thinking"
  | "responding"
  | "speaking"
  | "executing"
  | "error"
  | "interrupted";

export type Hue = "blue" | "violet" | "cyan" | "warn" | "danger" | "muted";
export type HoloTheme = "dark" | "light";

/** What a state looks like. Every value is a target the scene eases towards. */
export interface VisualProfile {
  a: Hue; // primary colour (core, avatar)
  b: Hue; // secondary colour (orbits, particles)
  energy: number; // 0..1 overall luminosity
  spin: number; // orbital rotation speed (rad/s)
  expand: number; // scale of the energy core while it is a sphere
  morph: number; // 0 = energy sphere, 1 = humanoid avatar fully materialised
  flow: number; // 0..1 activity of filaments and particles
  scan: number; // 0..1 visibility of the scanning band
}

/** Eased values handed to every layer once per frame. */
export interface Frame {
  time: number;
  spinPhase: number; // accumulated rotation (so speed changes never jump)
  flowPhase: number;
  energy: number;
  expand: number;
  morph: number;
  flow: number;
  scan: number;
  level: number; // real microphone level 0..1 (0 when no source is attached)
  react: number; // max(level, decaying pulse from real speech word boundaries)
  voice: number; // decaying pulse from real speech word boundaries only (drives the portrait's mouth)
  a: Color;
  b: Color;
  hi: Color; // highlight: luminous white on dark, deep ink on light
  light: boolean;
}

export interface Layer {
  readonly object: Object3D;
  update(frame: Frame): void;
}
