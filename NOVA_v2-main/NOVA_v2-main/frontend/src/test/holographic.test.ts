import { describe, expect, it } from "vitest";
import { STATE_LABELS, type AssistantState } from "../assistant/machine";
import { FIGURE_BOUNDS, FIGURE_STROKES, sampleFigure, strokePolylines } from "../hologram/holographic/figure";
import { PALETTES, PROFILES, VISUAL_STATE } from "../hologram/holographic/holographic.constants";

const states = Object.keys(STATE_LABELS) as AssistantState[];

describe("holographic core 2.0 — state mapping", () => {
  it("gives every real assistant state a visual family and a profile with colours of both themes", () => {
    for (const s of states) {
      expect(VISUAL_STATE[s], s).toBeDefined();
      const p = PROFILES[s];
      expect(p.morph).toBeGreaterThanOrEqual(0);
      expect(p.morph).toBeLessThanOrEqual(1);
      for (const theme of ["dark", "light"] as const) {
        expect(PALETTES[theme][p.a]).toMatch(/^#[0-9a-f]{6}$/);
        expect(PALETTES[theme][p.b]).toMatch(/^#[0-9a-f]{6}$/);
      }
    }
  });

  it("stays an energy core at rest and while listening, and materialises the avatar as NOVA works and answers", () => {
    expect(PROFILES.standby.morph).toBe(0);
    expect(PROFILES.listening.morph).toBe(0);
    expect(PROFILES.understanding.morph).toBeGreaterThan(0); // reacts as soon as a request is sent
    expect(PROFILES.thinking.morph).toBeGreaterThan(PROFILES.understanding.morph);
    expect(PROFILES.responding.morph).toBeGreaterThan(PROFILES.thinking.morph);
    expect(PROFILES.speaking.morph).toBe(1);
  });

  it("keeps thinking, speaking, executing and failure states visually distinct", () => {
    const key = (s: AssistantState) => JSON.stringify(PROFILES[s]);
    const distinct = ["standby", "listening", "thinking", "responding", "speaking", "executing", "error", "interrupted", "awaiting_approval"] as const;
    expect(new Set(distinct.map(key)).size).toBe(distinct.length);
    expect(PROFILES.error.a).toBe("danger");
    expect(PROFILES.awaiting_approval.a).toBe("warn");
  });
});

describe("holographic core 2.0 — figure", () => {
  it("is symmetric and stays inside its bounds", () => {
    for (const stroke of FIGURE_STROKES) {
      const lines = strokePolylines(stroke, 20);
      expect(lines).toHaveLength(stroke.mirror ? 2 : 1);
      for (const p of lines.flat()) {
        expect(p.x).toBeGreaterThanOrEqual(FIGURE_BOUNDS.minX);
        expect(p.x).toBeLessThanOrEqual(FIGURE_BOUNDS.maxX);
        expect(p.y).toBeGreaterThanOrEqual(FIGURE_BOUNDS.minY);
        expect(p.y).toBeLessThanOrEqual(FIGURE_BOUNDS.maxY);
      }
      if (stroke.mirror) lines[0]!.forEach((p, i) => expect(lines[1]![i]!.x).toBeCloseTo(-p.x));
    }
  });

  it("samples particle targets on the figure", () => {
    let seed = 1;
    const random = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    const pts = sampleFigure(200, random);
    expect(pts).toHaveLength(600);
    const m = 0.15; // particles scatter a little around the lines
    for (let i = 0; i < 200; i++) {
      expect(Math.abs(pts[i * 3]!)).toBeLessThan(FIGURE_BOUNDS.maxX + m);
      expect(pts[i * 3 + 1]!).toBeGreaterThan(FIGURE_BOUNDS.minY - m);
      expect(pts[i * 3 + 1]!).toBeLessThan(FIGURE_BOUNDS.maxY + m);
    }
  });
});
