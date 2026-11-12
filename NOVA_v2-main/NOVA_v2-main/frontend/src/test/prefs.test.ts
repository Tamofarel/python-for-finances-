import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCsrfToken } from "../api/client";
import { DEFAULT_PREFS, applyToDocument, resolveReducedMotion, resolveTheme, usePrefs } from "../store/prefs";
import css from "../styles.css?raw";
import { errorResponse, mockApi } from "./utils";

function media(matches: Record<string, boolean>) {
  vi.spyOn(window, "matchMedia").mockImplementation(((q: string) => ({
    matches: !!matches[q], media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia);
}

describe("theme and motion preferences", () => {
  beforeEach(() => {
    setCsrfToken("csrf");
    localStorage.clear();
    usePrefs.setState({ prefs: DEFAULT_PREFS, synced: true, error: null });
  });
  afterEach(() => vi.restoreAllMocks());

  it("follows the operating system unless the owner chose", () => {
    media({ "(prefers-color-scheme: light)": true, "(prefers-reduced-motion: reduce)": true });
    expect(resolveTheme({ theme: "system" })).toBe("light");
    expect(resolveTheme({ theme: "dark" })).toBe("dark");
    expect(resolveReducedMotion({ motion: "system" })).toBe(true);
    expect(resolveReducedMotion({ motion: "full" })).toBe(false);
    applyToDocument({ ...DEFAULT_PREFS, theme: "system", motion: "system" });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.motion).toBe("reduce");
  });

  it("applies a change immediately, saves it and caches it for the next visit", async () => {
    const { calls } = mockApi({ "PUT /api/preferences": () => ({ ...DEFAULT_PREFS, theme: "light", interaction_mode: "quiet" }) });
    await usePrefs.getState().update({ theme: "light", interaction_mode: "quiet" });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(calls[0]).toMatchObject({ method: "PUT", body: { theme: "light", interaction_mode: "quiet" } });
    expect(JSON.parse(localStorage.getItem("nova.preferences")!).theme).toBe("light");
    expect(usePrefs.getState().prefs.interaction_mode).toBe("quiet");
  });

  it("rolls back when the server refuses", async () => {
    mockApi({ "PUT /api/preferences": () => errorResponse(400, "consent_required", "Confirm the microphone notice first.") });
    applyToDocument(DEFAULT_PREFS);
    await expect(usePrefs.getState().update({ voice: { wake_enabled: true } })).rejects.toThrow(/microphone notice/);
    expect(usePrefs.getState().prefs.voice.wake_enabled).toBe(false);
    expect(usePrefs.getState().error).toMatch(/microphone notice/);
  });
});

// ---- contrast of the colour tokens in both themes (WCAG 2.1) -------------------------------------------------
function tokens(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  const block = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x! + 0.05) / (y! + 0.05);
}

describe("colour contrast", () => {
  const themes = { dark: tokens(css, ':root, :root[data-theme="dark"] {'), light: tokens(css, ':root[data-theme="light"] {') };

  it.each(Object.entries(themes))("%s theme text meets WCAG AA on every surface", (_name, t) => {
    for (const surface of ["abyss", "hull", "deck", "deck-2"]) {
      for (const fg of ["text", "muted", "signal", "aura", "ok", "warn", "danger"]) {
        expect(contrast(t[fg]!, t[surface]!), `${fg} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
    expect(contrast(t["signal-ink"]!, t["signal-fill"]!), "primary button").toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["aura-ink"]!, t["aura"]!), "listening button").toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["warn-ink"]!, t["warn"]!), "badge").toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["danger-ink"]!, t["danger"]!), "danger button").toBeGreaterThanOrEqual(4.5);
  });

  it("uses a designed light palette, not an inverted dark one", () => {
    expect(themes.light.abyss).not.toBe(themes.dark.text);
    expect(themes.light.signal).not.toBe(themes.dark.signal);
    expect(luminance(themes.light.deck!)).toBeGreaterThan(luminance(themes.light.abyss!)); // raised surfaces are lighter
  });
});

describe("loading preferences", () => {
  it("never overwrites a change the owner made while the load was in flight", async () => {
    setCsrfToken("csrf");
    usePrefs.setState({ prefs: DEFAULT_PREFS, synced: false, error: null });
    let release: (v: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return new Promise<Response>((r) => { release = r; });
      return new Response(JSON.stringify({ ...DEFAULT_PREFS, motion: "reduce" }), { headers: { "Content-Type": "application/json" } });
    }));
    const loading = usePrefs.getState().load();
    await usePrefs.getState().update({ motion: "reduce" });
    release(new Response(JSON.stringify(DEFAULT_PREFS), { headers: { "Content-Type": "application/json" } }));
    await loading;
    expect(usePrefs.getState().prefs.motion).toBe("reduce");
    expect(document.documentElement.dataset.motion).toBe("reduce");
    vi.unstubAllGlobals();
  });
});
