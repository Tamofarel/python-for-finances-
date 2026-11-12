// Voice settings: wake phrase, transcription engine, spoken replies and troubleshooting. Every option says where
// audio or text is processed.

import { Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PhraseCheck, SpotifyStatus, SttEngine, VoicePrefs } from "../../api/types";
import { WakeConsentDialog, micLabel } from "../../components/VoiceStatus";
import { Field, Notice } from "../../components/ui";
import { usePrefs } from "../../store/prefs";
import { useVoice } from "../../store/voice";
import type { VoiceInfo } from "../../voice/types";

const SENSITIVITY: { id: VoicePrefs["sensitivity"]; label: string; hint: string }[] = [
  { id: "low", label: "Fewer false alarms", hint: "You may need to repeat the phrase or speak closer to the microphone." },
  { id: "medium", label: "Balanced", hint: "Recommended for a quiet room." },
  { id: "high", label: "Responds more easily", hint: "Also more likely to wake on similar-sounding words." },
];

const LANGS = [
  ["en-US", "English (United States)"], ["en-GB", "English (United Kingdom)"], ["fr-FR", "Français (France)"],
  ["fr-CA", "Français (Canada)"], ["es-ES", "Español"], ["de-DE", "Deutsch"], ["it-IT", "Italiano"], ["pt-BR", "Português (Brasil)"],
] as const;

function useVoices(): VoiceInfo[] {
  const output = useVoice((s) => s.output);
  const [voices, setVoices] = useState<VoiceInfo[]>(() => output.voices());
  useEffect(() => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const on = () => setVoices(output.voices());
    on();
    window.speechSynthesis.addEventListener?.("voiceschanged", on);
    return () => window.speechSynthesis.removeEventListener?.("voiceschanged", on);
  }, [output]);
  return voices;
}

function PhraseEditor() {
  const voicePrefs = usePrefs((s) => s.prefs.voice);
  const setVoice = usePrefs((s) => s.setVoice);
  const [phrase, setPhrase] = useState(voicePrefs.wake_phrase);
  const [check, setCheck] = useState<PhraseCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setPhrase(voicePrefs.wake_phrase), [voicePrefs.wake_phrase]);
  useEffect(() => {
    setSaved(false);
    if (!phrase.trim()) return setCheck(null);
    const t = setTimeout(() => {
      api.voice.checkPhrase(phrase).then(setCheck).catch((e: Error) => setError(e.message));
    }, 350);
    return () => clearTimeout(t);
  }, [phrase]);

  async function save() {
    setError(null);
    try {
      await setVoice({ wake_phrase: phrase });
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const changed = check?.phrase && check.phrase !== voicePrefs.wake_phrase;
  return (
    <div className="stack-sm">
      <Field label="Wake phrase" hint="One to four English words that the local detector knows. Two or more words cause far fewer false alarms.">
        <input value={phrase} onChange={(e) => setPhrase(e.target.value)} maxLength={40} autoComplete="off" />
      </Field>
      {check && !check.valid && <Notice>{check.message}</Notice>}
      {check?.valid && check.message && <Notice kind="warn">{check.message}</Notice>}
      {error && <Notice>{error}</Notice>}
      <div className="row">
        <button type="button" className="btn btn-sm" disabled={!check?.valid || !changed} onClick={() => void save()}>Use this phrase</button>
        {saved && <span className="small ok-text">Saved.</span>}
      </div>
    </div>
  );
}

export function VoiceSettings() {
  const voice = useVoice();
  const loadCapabilities = useVoice((s) => s.loadCapabilities);
  const refreshPermission = useVoice((s) => s.refreshPermission);
  const prefs = usePrefs((s) => s.prefs);
  const setVoice = usePrefs((s) => s.setVoice);
  const v = prefs.voice;
  const voices = useVoices();
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [spotify, setSpotify] = useState<SpotifyStatus | null>(null);
  const caps = voice.capabilities;

  useEffect(() => {
    void loadCapabilities();
    void refreshPermission();
    void api.spotify.status().then(setSpotify).catch(() => setSpotify(null));
  }, [loadCapabilities, refreshPermission, v.lang]);

  async function change(patch: Partial<VoicePrefs>) {
    setError(null);
    try {
      await setVoice(patch);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function toggleWake(on: boolean) {
    if (!on) void voice.micOff();
    else if (prefs.wake_consent_at) void voice.enableWake();
    else setConsent(true);
  }

  const engines = caps?.stt_engines ?? [];
  const lang = v.lang.slice(0, 2);
  const sameLang = voices.filter((x) => x.lang.toLowerCase().startsWith(lang));
  const chosen = voices.find((x) => x.uri === v.tts_voice);

  return (
    <div className="stack settings-section">
      <section className="stack-sm">
        <h2 className="h3">Microphone</h2>
        <p className={`mic-line mic-${voice.listening ? "listening" : voice.mic}`}><span className="mic-dot" aria-hidden /> {micLabel(voice.mic, voice.listening, v.wake_phrase)}</p>
        {voice.micMessage && <Notice kind="warn">{voice.micMessage}</Notice>}
        <p className="small muted">
          Browser permission: <strong>{{ granted: "allowed", denied: "blocked", prompt: "asks when needed", unknown: "unknown", unsupported: "not available" }[voice.micPermission]}</strong>.
          {voice.micPermission === "denied" && " Allow the microphone for this site in the address bar (the lock or camera icon), then press Try again."}
        </p>
        {(voice.mic === "denied" || voice.mic === "error") && <div><button type="button" className="btn btn-sm" onClick={() => void voice.retryMic()}>Try again</button></div>}
      </section>

      <section className="stack-sm">
        <h2 className="h3">Collaboration style</h2>
        <fieldset className="choice">
          <legend className="sr-only">Progress update style</legend>
          {([
            ["collaborative", "Collaborative", "Concise progress updates; redirect the approach at any time."],
            ["quiet", "Quiet execution", "Keep progress visible on screen and speak only when a decision or answer needs you."],
            ["step_by_step", "Step by step", "Run one workflow stage at a time and stop at its decision point."],
          ] as const).map(([id, label, detail]) => (
            <label key={id} className="choice-row">
              <input type="radio" name="interaction-mode" checked={prefs.interaction_mode === id}
                onChange={() => void usePrefs.getState().update({ interaction_mode: id })} />
              <span><strong>{label}</strong><span className="small muted">{detail}</span></span>
            </label>
          ))}
        </fieldset>
      </section>

      <section className="stack-sm">
        <h2 className="h3">Wake phrase</h2>
        {caps && !caps.wake.available && <Notice>{caps.wake.reason}</Notice>}
        {caps && !caps.enabled && <Notice>Voice is turned off on this server (NOVA_VOICE_ENABLED=false).</Notice>}
        <label className="choice-row">
          <input type="checkbox" checked={v.wake_enabled} disabled={!!caps && (!caps.wake.available || !caps.enabled)} onChange={(e) => toggleWake(e.target.checked)} />
          <span>
            <strong>Listen for “{v.wake_phrase}”</strong>
            <span className="small muted">
              The wake phrase is detected by NOVA's backend on this computer (PocketSphinx, US English). The microphone stays
              open while this is on; audio is processed in memory and never stored. Push-to-talk works whether or not this is on.
            </span>
          </span>
        </label>
        <PhraseEditor />
        <fieldset className="choice">
          <legend>Sensitivity</legend>
          {SENSITIVITY.map((s) => (
            <label key={s.id} className="choice-row">
              <input type="radio" name="sensitivity" checked={v.sensitivity === s.id} onChange={() => void change({ sensitivity: s.id })} />
              <span><strong>{s.label}</strong><span className="small muted">{s.hint}</span></span>
            </label>
          ))}
        </fieldset>
        <label className="choice-row">
          <input type="checkbox" checked={v.chime} onChange={(e) => void change({ chime: e.target.checked })} />
          <span><strong>Play a short tone when the wake phrase is heard</strong></span>
        </label>
      </section>

      <section className="stack-sm">
        <h2 className="h3">Speech recognition</h2>
        <fieldset className="choice">
          <legend className="sr-only">Transcription engine</legend>
          <label className={`choice-row ${!voice.input.supported ? "disabled" : ""}`}>
            <input type="radio" name="stt" checked={v.stt_engine === "browser"} disabled={!voice.input.supported} onChange={() => void change({ stt_engine: "browser" })} />
            <span>
              <strong>Browser speech recognition</strong>
              <span className="small muted">{voice.input.supported
                ? "Most accurate and supports many languages. Chrome and Edge send the audio of what you say to Google or Microsoft."
                : "Not available in this browser (Firefox has none). Choose a local engine."}</span>
            </span>
          </label>
          {engines.filter((e) => e.id !== "browser").map((e) => (
            <label key={e.id} className={`choice-row ${!e.available ? "disabled" : ""}`}>
              <input type="radio" name="stt" checked={v.stt_engine === e.id} disabled={!e.available} onChange={() => void change({ stt_engine: e.id as SttEngine })} />
              <span><strong>{e.label}</strong><span className="small muted">{e.available ? e.note : e.reason}</span></span>
            </label>
          ))}
        </fieldset>
        <Field label="Language" hint="Used by browser recognition, Vosk model selection and spoken replies. PocketSphinx transcription is English only.">
          <select value={v.lang} onChange={(e) => void change({ lang: e.target.value })}>
            {LANGS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            {!LANGS.some(([id]) => id === v.lang) && <option value={v.lang}>{v.lang}</option>}
          </select>
        </Field>
      </section>

      <section className="stack-sm">
        <h2 className="h3">Spotify</h2>
        <p className="small muted">Optional. NOVA uses Spotify's user-authorized playback API when connected. Your password is entered only on Spotify.</p>
        {spotify?.connected ? (
          <div className="row wrap">
            <span className="badge badge-done">Spotify connected</span>
            <button type="button" className="btn btn-sm" onClick={() => { window.location.href = "/api/spotify/connect"; }}>
              Reconnect Spotify
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void api.spotify.disconnect().then(() => setSpotify({ ...spotify, connected: false }))}>Disconnect</button>
          </div>
        ) : (
          <>
            {spotify?.reason && <Notice>{spotify.reason}</Notice>}
            <button type="button" className="btn btn-sm" disabled={!spotify?.configured}
              onClick={() => { window.location.href = "/api/spotify/connect"; }}>Connect Spotify</button>
            <p className="small muted">Spotify playback requires an authorized account, a ready playback device, and an eligible Spotify plan. Playback is confirmed from Spotify's reported state.</p>
          </>
        )}
      </section>

      <section className="stack-sm">
        <h2 className="h3">Spoken replies</h2>
        {!voice.output.supported && <Notice kind="warn">This browser cannot speak. Replies stay on screen.</Notice>}
        <label className="choice-row">
          <input type="checkbox" checked={v.speak_replies} onChange={(e) => void change({ speak_replies: e.target.checked })} />
          <span><strong>Answer voice requests aloud</strong><span className="small muted">Typed messages are not read out; use “Read aloud” on any reply.</span></span>
        </label>
        <label className="choice-row">
          <input type="checkbox" checked={v.acknowledge} onChange={(e) => void change({ acknowledge: e.target.checked })} />
          <span><strong>Say “Okay” when NOVA has your request</strong></span>
        </label>
        <label className="choice-row">
          <input type="checkbox" checked={v.follow_up} onChange={(e) => void change({ follow_up: e.target.checked })} />
          <span>
            <strong>Listen for a follow-up after answering</strong>
            <span className="small muted">After a spoken answer, NOVA listens once more for a few seconds without the wake phrase. Requires the wake phrase to be on.</span>
          </span>
        </label>
        <Field label="Voice" hint={chosen && !chosen.local ? "This voice is online: the text of replies is sent to the browser's voice service." : "Voices marked “on this device” never send text anywhere."}>
          <div className="row">
            <select aria-label="Voice for spoken replies" value={v.tts_voice ?? ""} onChange={(e) => void change({ tts_voice: e.target.value || null })}>
              <option value="">Best voice on this device for the language</option>
              {sameLang.map((x) => <option key={x.uri} value={x.uri}>{x.name} — {x.local ? "on this device" : "online"}</option>)}
            </select>
            <button type="button" className="btn btn-sm" disabled={!voice.output.supported} onClick={() => voice.speak("Hello, I'm NOVA. This is how I sound.")}>
              <Play size={13} aria-hidden /> Hear it
            </button>
          </div>
        </Field>
      </section>

      <section className="stack-sm">
        <h2 className="h3">Privacy and troubleshooting</h2>
        <ul className="small muted bullets">
          <li>Microphone audio goes only to NOVA's backend on this computer, which refuses audio from other machines unless <code>NOVA_VOICE_ALLOW_REMOTE</code> is set.</li>
          <li>Audio is never written to disk. Transcripts become your messages; the audit log records only when the microphone started and stopped.</li>
          <li>If NOVA does not react to the wake phrase, speak a little closer, try “Responds more easily”, or use a two-word phrase.</li>
          <li>If the browser says the microphone is blocked, allow it in the site settings and press Try again. On macOS also allow the browser under System Settings → Privacy &amp; Security → Microphone; on Windows under Settings → Privacy → Microphone.</li>
        </ul>
      </section>
      {error && <Notice>{error}</Notice>}
      {consent && <WakeConsentDialog onClose={() => setConsent(false)} />}
    </div>
  );
}
