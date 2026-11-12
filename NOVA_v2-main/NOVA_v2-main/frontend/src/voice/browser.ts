// Browser (Web Speech API) implementations of the voice adapters.

import type { RecognitionHandlers, SpeechInputAdapter, SpeechOutputAdapter, UtteranceHandlers, VoiceInfo } from "./types";

interface RecognitionResultEvent {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
}

interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onstart: (() => void) | null;
  onresult: ((e: RecognitionResultEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const RECOGNITION_ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access was denied. Allow it in your browser's site settings.",
  "service-not-allowed": "Speech recognition is blocked in this browser.",
  "no-speech": "No speech was detected.",
  "audio-capture": "No microphone was found.",
  network: "This browser's speech recognition needs a network connection.",
  aborted: "Listening was stopped.",
};

export class BrowserSpeechInput implements SpeechInputAdapter {
  readonly name = "browser";
  readonly privacyNote =
    "Speech is transcribed by your browser. In Chrome and Edge the audio is sent to the browser vendor's speech service.";
  private rec: Recognition | null = null;

  get supported(): boolean {
    return recognitionCtor() !== null;
  }

  start(lang: string, h: RecognitionHandlers, opts: { continuous?: boolean } = {}): void {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      h.onError("Voice input is not supported in this browser. Text input still works.");
      h.onEnd();
      return;
    }
    this.rec?.abort();
    const rec = new Ctor();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = opts.continuous ?? true; // push-to-talk keeps listening until released
    let finalText = "";
    rec.onstart = () => h.onStart?.();
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (!r) continue;
        const t = r[0]?.transcript ?? "";
        if (r.isFinal) finalText += t;
        else interim += t;
      }
      h.onInterim((finalText + interim).trim());
    };
    rec.onerror = (e) => {
      // "no-speech" simply ends with an empty transcript (common after the wake phrase); it is not an error.
      if (e.error !== "aborted" && e.error !== "no-speech") h.onError(RECOGNITION_ERRORS[e.error] ?? `Voice input error: ${e.error}`);
    };
    rec.onend = () => {
      this.rec = null;
      if (finalText.trim()) h.onFinal(finalText.trim());
      h.onEnd();
    };
    this.rec = rec;
    rec.start();
  }

  stop(): void {
    this.rec?.stop();
  }

  abort(): void {
    this.rec?.abort();
  }
}

export class BrowserSpeechOutput implements SpeechOutputAdapter {
  readonly name = "browser";
  voiceURI: string | null = null;

  get supported(): boolean {
    return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  }

  voices(): VoiceInfo[] {
    if (!this.supported) return [];
    return window.speechSynthesis.getVoices().map((v) => ({ uri: v.voiceURI, name: v.name, lang: v.lang, local: v.localService }));
  }

  /** The chosen voice, else a voice for the language that runs on this device, else the browser default. */
  private pick(lang: string): SpeechSynthesisVoice | null {
    const all = window.speechSynthesis.getVoices();
    if (this.voiceURI) {
      const chosen = all.find((v) => v.voiceURI === this.voiceURI);
      if (chosen) return chosen;
    }
    const base = lang.slice(0, 2).toLowerCase();
    const sameLang = all.filter((v) => v.lang.toLowerCase().startsWith(base));
    return sameLang.find((v) => v.localService && v.lang === lang) ?? sameLang.find((v) => v.localService) ?? null;
  }

  speak(text: string, lang: string, h: UtteranceHandlers): void {
    if (!this.supported || !text.trim()) {
      h.onEnd?.();
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    const voice = this.pick(lang);
    if (voice) u.voice = voice;
    u.lang = voice?.lang ?? lang;
    u.rate = 1.03;
    u.onstart = () => h.onStart?.();
    u.onend = () => h.onEnd?.();
    u.onerror = (e) => {
      if (e.error !== "canceled" && e.error !== "interrupted") h.onError?.(`Speech output error: ${e.error}`);
      h.onEnd?.();
    };
    u.onboundary = () => h.onBoundary?.();
    window.speechSynthesis.speak(u);
  }

  cancel(): void {
    if (this.supported) window.speechSynthesis.cancel();
  }
}
