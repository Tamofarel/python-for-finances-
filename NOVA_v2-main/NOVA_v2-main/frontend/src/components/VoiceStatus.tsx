// Microphone status, wake-phrase consent and theme switch. These sit in the navigation rail so the owner can
// always see whether NOVA is listening and turn the microphone off with one click.

import { Mic, MicOff, Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import type { SttEngine, ThemePref } from "../api/types";
import { usePrefs } from "../store/prefs";
import { type MicState, useVoice } from "../store/voice";
import { Modal, Notice } from "./ui";

export function micLabel(mic: MicState, listening: boolean, phrase: string): string {
  if (listening) return "Listening to you now";
  switch (mic) {
    case "armed":
      return `Listening for “${phrase}” on this computer`;
    case "starting":
      return "Starting the microphone…";
    case "suspended":
      return "Click anywhere so the browser lets NOVA listen";
    case "denied":
      return "Microphone blocked by the browser";
    case "error":
      return "Microphone problem";
    default:
      return "Microphone off";
  }
}

/** Explains exactly what turning on the wake phrase does, and records consent on the server. */
export function WakeConsentDialog({ onClose }: { onClose: () => void }) {
  const voice = useVoice();
  const prefs = usePrefs((s) => s.prefs);
  const caps = voice.capabilities;
  const engines = caps?.stt_engines ?? [];
  const browserOk = voice.input.supported;
  const localOk = engines.some((e) => e.id !== "browser" && e.available);
  const [stt, setStt] = useState<SttEngine>(() =>
    prefs.voice.stt_engine !== "browser" || !browserOk ? (engines.find((e) => e.id !== "browser" && e.available)?.id as SttEngine ?? "browser") : "browser");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wakeOk = caps?.wake.available ?? true;

  async function turnOn() {
    setBusy(true);
    setError(null);
    const ok = await voice.enableWake({ consent: true, stt });
    setBusy(false);
    if (ok) onClose();
    else setError(useVoice.getState().micMessage ?? usePrefs.getState().error ?? "Could not turn on the wake phrase.");
  }

  return (
    <Modal title={`Listen for “${prefs.voice.wake_phrase}”?`} onClose={onClose} footer={
      <>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Not now</button>
        <button type="button" className="btn btn-primary" disabled={busy || !wakeOk} onClick={() => void turnOn()}>
          {busy ? "Turning on…" : "Turn on the microphone"}
        </button>
      </>
    }>
      <div className="stack consent">
        {!wakeOk && <Notice>{caps?.wake.reason ?? "Wake-phrase detection is not available on this computer."}</Notice>}
        <p>
          While this is on, your microphone stays open and its audio goes to NOVA's backend <strong>on this computer</strong>,
          which listens only for the wake phrase. The audio is processed in memory and never saved or sent anywhere else.
        </p>
        <p>The microphone icon in the sidebar shows when NOVA is listening. Click it at any time to turn the microphone off.</p>
        <fieldset className="choice">
          <legend>After the wake phrase, transcribe what you say with</legend>
          <label className={`choice-row ${!browserOk ? "disabled" : ""}`}>
            <input type="radio" name="stt" checked={stt === "browser"} disabled={!browserOk} onChange={() => setStt("browser")} />
            <span>
              <strong>The browser's speech recognition</strong>
              <span className="small muted">
                {browserOk
                  ? "Most accurate. In Chrome and Edge, the words you say after the wake phrase are sent to Google or Microsoft for transcription."
                  : "Not available in this browser."}
              </span>
            </span>
          </label>
          {engines.filter((e) => e.id !== "browser").map((e) => (
            <label key={e.id} className={`choice-row ${!e.available ? "disabled" : ""}`}>
              <input type="radio" name="stt" checked={stt === e.id} disabled={!e.available} onChange={() => setStt(e.id as SttEngine)} />
              <span>
                <strong>{e.label}</strong>
                <span className="small muted">{e.available ? e.note : e.reason}</span>
              </span>
            </label>
          ))}
          {!browserOk && !localOk && <Notice kind="warn">No speech recognition is available. You can still type, and NOVA can still speak.</Notice>}
        </fieldset>
        {error && <Notice>{error}</Notice>}
      </div>
    </Modal>
  );
}

/** Sidebar microphone button: shows the real microphone state and turns listening on or off. */
export function MicStatusButton() {
  const voice = useVoice();
  const voicePrefs = usePrefs((s) => s.prefs.voice);
  const consented = usePrefs((s) => !!s.prefs.wake_consent_at);
  const prefsSynced = usePrefs((s) => s.synced);
  const [consent, setConsent] = useState(false);
  useEffect(() => {
    if (!prefsSynced || consented || sessionStorage.getItem("nova.wake-prompt-dismissed") === "true") return;
    setConsent(true);
  }, [prefsSynced, consented]);
  const label = micLabel(voice.mic, voice.listening, voicePrefs.wake_phrase);
  const on = voice.mic === "armed" || voice.mic === "suspended" || voice.listening;
  const problem = voice.mic === "denied" || voice.mic === "error";

  async function click() {
    // Decide with the owner's saved preferences, not the browser cache, so consent is never asked twice.
    if (!usePrefs.getState().synced) await usePrefs.getState().load();
    if (on || voice.mic === "starting") {
      void voice.micOff();
    } else if (problem) {
      void voice.retryMic();
    } else if (consented || usePrefs.getState().prefs.wake_consent_at) {
      void voice.enableWake();
    } else {
      void voice.loadCapabilities();
      setConsent(true);
    }
  }

  const action = on ? "Turn the microphone off" : problem ? "Try the microphone again" : `Listen for “${voicePrefs.wake_phrase}”`;
  return (
    <>
      <button type="button" className={`rail-btn mic-btn mic-${voice.listening ? "listening" : voice.mic}`} onClick={() => void click()}
        aria-pressed={on} aria-label={`${label}. ${action}.`} data-tip={`${label} — ${action.toLowerCase()}`}>
        {on || voice.mic === "starting" ? <Mic size={18} aria-hidden /> : <MicOff size={18} aria-hidden />}
        <span className="mic-dot" aria-hidden />
      </button>
      {consent && <WakeConsentDialog onClose={() => {
        sessionStorage.setItem("nova.wake-prompt-dismissed", "true");
        setConsent(false);
      }} />}
    </>
  );
}

const NEXT_THEME: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" };
const THEME_LABEL: Record<ThemePref, string> = { system: "Theme: follows your system", light: "Theme: light", dark: "Theme: dark" };

export function ThemeToggle() {
  const theme = usePrefs((s) => s.prefs.theme);
  const update = usePrefs((s) => s.update);
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <button type="button" className="rail-btn" onClick={() => void update({ theme: NEXT_THEME[theme] }).catch(() => undefined)}
      aria-label={`${THEME_LABEL[theme]}. Switch to ${NEXT_THEME[theme]}.`} data-tip={`${THEME_LABEL[theme]} — click for ${NEXT_THEME[theme]}`}>
      <Icon size={18} aria-hidden />
    </button>
  );
}
