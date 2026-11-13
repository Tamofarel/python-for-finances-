// The humanoid figure, drawn in code as luminous line art (no image, no model file): long flowing hair, a dark
// faceless face with a crisp contour, neck, collarbones, shoulders and arms, and energy lines that run into the core
// at the chest. Strokes are defined for the right half and mirrored. Coordinates are scene units.

import { SplineCurve, Vector2 } from "three";
import { CHEST_Y } from "./holographic.constants";

export interface Stroke {
  points: [number, number][];
  width: number; // half-width of the luminous ribbon
  glow: number; // 0..1 brightness of this line
  order: number; // 0..1: when the stroke starts to draw during the core-to-avatar transformation
  span: number; // how long it takes to draw
  mirror: boolean;
}

/** Small deterministic generator, so the figure is identical on every load (and testable). */
function seeded(seed: number): () => number {
  let s = seed >>> 0;
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296;
}

const S = (points: [number, number][], width: number, glow: number, order: number, span = 0.25): Stroke =>
  ({ points, width, glow, order, span, mirror: true });

/** Right half of the face, from the hairline to a slender chin. */
function faceOutline(): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i <= 14; i++) {
    const t = 0.35 + (i / 14) * (Math.PI - 0.35);
    const k = Math.max(0, (t - Math.PI * 0.45) / (Math.PI * 0.55));
    pts.push([0.46 * Math.sin(t) * (1 - 0.55 * k * k), 1.5 + 0.74 * Math.cos(t)]);
  }
  return pts;
}

/** Long hair: strands from the parting, over the skull, falling past the face onto the shoulders. */
function hair(count: number, random: () => number): Stroke[] {
  const out: Stroke[] = [];
  for (let i = 0; i < count; i++) {
    const h = i / (count - 1); // 0 = next to the face, 1 = outermost
    const j = () => (random() - 0.5) * 0.14;
    const sway = (random() - 0.5) * 0.3; // strands do not fall in parallel
    const out_ = 0.04 + h * 0.3;
    const fall = 0.55 + h * 0.9 + random() * 0.35;
    out.push(S([
      [0.03 + random() * 0.22, 2.6 + random() * 0.06],
      [0.36 + out_ * 0.6 + j() * 0.5, 2.5 + out_ * 0.18],
      [0.6 + out_ + j(), 2.0 + j()],
      [0.64 + out_ * 1.15 + j(), 1.3 + j()],
      [0.66 + out_ * 1.35 + j() + sway * 0.3, 0.6 + j()],
      [0.78 + out_ * 1.8 + sway, -0.1 - fall * 0.4],
      [0.82 + out_ * 2.0 + sway * 1.6 + random() * 0.2, -0.2 - fall],
    ], 0.012 + random() * 0.012, 0.5 + random() * 0.5, 0.45 + h * 0.25 + random() * 0.05, 0.3));
  }
  return out;
}

// Reveal order: energy runs out of the core, the body forms, then the neck and face, the hair last.
export const FIGURE_STROKES: Stroke[] = [
  // energy lines into the core
  S([[0, CHEST_Y], [0.06, -0.3], [0.04, 0.2]], 0.016, 0.8, 0),
  S([[0, CHEST_Y], [0.25, -0.5], [0.6, -0.2], [1.1, 0.05]], 0.018, 0.9, 0),
  S([[0, CHEST_Y], [0.3, -1.2], [0.7, -1.9], [0.95, -2.6]], 0.016, 0.7, 0.03),
  // body
  S([[0.3, 0.2], [0.4, -0.4], [0.34, -1.3], [0.24, -2.4], [0.2, -3.3]], 0.018, 0.75, 0.1),
  S([[0.95, 0.15], [0.9, -0.8], [0.8, -2.0], [0.74, -3.3]], 0.012, 0.45, 0.14),
  S([[0.2, -2.3], [0.7, -2.24], [1.15, -2.12]], 0.01, 0.35, 0.18),
  S([[1.3, -0.7], [1.22, -1.9], [1.18, -3.3]], 0.02, 0.7, 0.16),
  S([[0.3, 0.24], [0.9, 0.16], [1.45, 0.0], [1.82, -0.45], [1.95, -1.4], [2.02, -3.3]], 0.032, 1, 0.18, 0.3),
  S([[1.45, -0.55], [1.52, -1.6], [1.5, -3.3]], 0.014, 0.55, 0.22),
  // neck and collarbones
  S([[0.06, 0.12], [0.45, 0.06], [0.95, 0.18]], 0.016, 0.8, 0.3),
  S([[0.2, 0.98], [0.21, 0.55], [0.3, 0.22]], 0.02, 0.9, 0.32),
  S([[0.09, 0.86], [0.1, 0.45], [0.04, 0.2]], 0.008, 0.4, 0.34),
  // face: a crisp contour, a few faint planes, no features
  S(faceOutline(), 0.022, 1, 0.38, 0.3),
  S([[0.4, 1.35], [0.28, 1.1], [0.1, 0.86]], 0.009, 0.45, 0.5),
  S([[0.1, 1.6], [0.3, 1.58], [0.42, 1.45]], 0.008, 0.35, 0.55),
  // strands framing the face
  S([[0.04, 2.5], [0.3, 2.3], [0.48, 1.85], [0.52, 1.3], [0.5, 0.85], [0.6, 0.35], [0.75, -0.1]], 0.016, 1, 0.48, 0.3),
  S([[0.08, 2.55], [0.4, 2.35], [0.56, 1.9], [0.6, 1.2], [0.6, 0.6], [0.72, 0.1]], 0.012, 0.8, 0.5, 0.3),
  ...hair(22, seeded(7)),
];

export const FIGURE_BOUNDS = { minX: -2.2, maxX: 2.2, minY: -3.45, maxY: 2.75 } as const;

/** Right half of the filled silhouette (hair mass, shoulders, body); mirrored by the avatar. */
export const SILHOUETTE: [number, number][] = [
  [0, 2.68], [0.45, 2.6], [0.8, 2.2], [0.97, 1.4], [1.06, 0.6], [1.25, 0.15], [1.6, 0.0], [1.88, -0.45], [2.0, -1.4], [2.06, -3.45], [0, -3.45],
];

/** A stroke as evenly spaced points (both halves when mirrored). */
export function strokePolylines(stroke: Stroke, segments: number): Vector2[][] {
  const right = new SplineCurve(stroke.points.map(([x, y]) => new Vector2(x, y))).getSpacedPoints(segments);
  return stroke.mirror ? [right, right.map((p) => new Vector2(-p.x, p.y))] : [right];
}

/** Points scattered on the figure: where energy particles settle when the avatar forms ("sand" along the lines,
 *  denser in the hair). */
export function sampleFigure(count: number, random: () => number = Math.random): Float32Array {
  const lines = FIGURE_STROKES.flatMap((s) => strokePolylines(s, 40));
  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const line = lines[Math.floor(random() * lines.length)]!;
    const p = line[Math.floor(random() * line.length)]!;
    const spread = random() < 0.4 ? 0.12 : 0.03;
    out[i * 3] = p.x + (random() - 0.5) * 2 * spread;
    out[i * 3 + 1] = p.y + (random() - 0.5) * 2 * spread;
    out[i * 3 + 2] = (random() - 0.5) * 0.3;
  }
  return out;
}
