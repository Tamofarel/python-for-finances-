// Owner preferences: theme, motion, voice and proactivity. Stored on the server (per user) and cached in this
// browser so the right theme applies before sign-in. The cache is a convenience; the server copy is authoritative.

import { useEffect, useState } from "react";
import { create } from "zustand";
import { ApiError, api } from "../api/client";
import type { Preferences, PreferencesUpdate, VoicePrefs } from "../api/types";

const CACHE_KEY = "nova.preferences";

export const DEFAULT_VOICE: VoicePrefs = {
  wake_enabled: false,
  wake_phrase: "hey nova",
  sensitivity: "medium",
  stt_engine: "browser",
  tts_voice: null,
  speak_replies: true,
  acknowledge: true,
  chime: true,
  follow_up: false,
  lang: (typeof navigator !== "undefined" && /^[a-z]{2}(-[A-Z]{2})?$/.test(navigator.language) ? navigator.language : "en-US"),
};

export const DEFAULT_PREFS: Preferences = {
  theme: "system",
  motion: "system",
  suggestions: true,
  interaction_mode: "collaborative",
  voice: DEFAULT_VOICE,
  wake_consent_at: null,
};

function readCache(): Preferences {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const p = JSON.parse(raw) as Partial<Preferences>;
    return { ...DEFAULT_PREFS, ...p, voice: { ...DEFAULT_VOICE, ...(p.voice ?? {}) } };
  } catch {
    return DEFAULT_PREFS;
  }
}

function writeCache(p: Preferences): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(p));
  } catch {
    /* storage unavailable (private window): preferences still apply for this session */
  }
}

function media(query: string): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.(query).matches;
}

export function resolveTheme(p: Pick<Preferences, "theme">): "light" | "dark" {
  if (p.theme === "light" || p.theme === "dark") return p.theme;
  return media("(prefers-color-scheme: light)") ? "light" : "dark";
}

export function resolveReducedMotion(p: Pick<Preferences, "motion">): boolean {
  if (p.motion === "reduce") return true;
  if (p.motion === "full") return false;
  return media("(prefers-reduced-motion: reduce)");
}

/** Apply theme and motion to <html> so CSS tokens and animations follow the preference. */
export function applyToDocument(p: Preferences): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(p);
  root.dataset.motion = resolveReducedMotion(p) ? "reduce" : "full";
  const meta = document.querySelector('meta[name="color-scheme"]');
  meta?.setAttribute("content", root.dataset.theme);
}

interface PrefsState {
  prefs: Preferences;
  synced: boolean; // loaded from the server for the signed-in owner
  error: string | null;
  load: () => Promise<void>;
  update: (patch: PreferencesUpdate) => Promise<Preferences>;
  setVoice: (patch: Partial<VoicePrefs>) => Promise<Preferences>;
}

let edits = 0; // changes made in this page; a slower load() must not overwrite them

export const usePrefs = create<PrefsState>((set, get) => ({
  prefs: readCache(),
  synced: false,
  error: null,
  load: async () => {
    const editsAtStart = edits;
    try {
      const prefs = await api.preferences.get();
      if (edits !== editsAtStart) {
        set({ synced: true }); // the owner changed something meanwhile; their newer choice wins
        return;
      }
      writeCache(prefs);
      applyToDocument(prefs);
      set({ prefs, synced: true, error: null });
    } catch (e) {
      set({ error: (e as Error).message });
    }
  },
  update: async (patch) => {
    edits += 1;
    const before = get().prefs;
    // Apply immediately so the change is visible; roll back if the server refuses it.
    const optimistic: Preferences = {
      ...before,
      ...(patch.theme ? { theme: patch.theme } : {}),
      ...(patch.motion ? { motion: patch.motion } : {}),
      ...(patch.suggestions !== undefined ? { suggestions: patch.suggestions } : {}),
      ...(patch.interaction_mode ? { interaction_mode: patch.interaction_mode } : {}),
      voice: { ...before.voice, ...(patch.voice ?? {}) },
    };
    applyToDocument(optimistic);
    set({ prefs: optimistic });
    try {
      const saved = await api.preferences.update(patch);
      writeCache(saved);
      applyToDocument(saved);
      set({ prefs: saved, error: null });
      return saved;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        writeCache(optimistic); // not signed in: remember the choice in this browser only
        return optimistic;
      }
      applyToDocument(before);
      set({ prefs: before, error: (e as Error).message });
      throw e;
    }
  },
  setVoice: (patch) => get().update({ voice: patch }),
}));

/** Re-apply when the OS theme or motion setting changes (only matters for "system"). */
export function watchSystemPreferences(): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const queries = [window.matchMedia("(prefers-color-scheme: light)"), window.matchMedia("(prefers-reduced-motion: reduce)")];
  const onChange = () => applyToDocument(usePrefs.getState().prefs);
  queries.forEach((q) => q.addEventListener?.("change", onChange));
  return () => queries.forEach((q) => q.removeEventListener?.("change", onChange));
}

/** The theme actually shown, reactive to both the preference and the OS setting. */
export function useResolvedTheme(): "light" | "dark" {
  const theme = usePrefs((s) => s.prefs.theme);
  const [resolved, setResolved] = useState(() => resolveTheme({ theme }));
  useEffect(() => {
    setResolved(resolveTheme({ theme }));
    if (theme !== "system" || !window.matchMedia) return;
    const q = window.matchMedia("(prefers-color-scheme: light)");
    const on = () => setResolved(resolveTheme({ theme }));
    q.addEventListener?.("change", on);
    return () => q.removeEventListener?.("change", on);
  }, [theme]);
  return resolved;
}

export function useReducedMotion(): boolean {
  const motion = usePrefs((s) => s.prefs.motion);
  return resolveReducedMotion({ motion });
}
