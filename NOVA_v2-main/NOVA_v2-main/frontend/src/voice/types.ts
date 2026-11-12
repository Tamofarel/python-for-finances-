// Voice adapters. The UI only talks to these interfaces, so browser speech can later be replaced by local
// engines (e.g. Whisper for recognition, Piper for synthesis) without touching the conversation code.

export interface RecognitionHandlers {
  onStart?: () => void; // the recogniser is actually capturing
  onInterim: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  onEnd: () => void; // always called once when recognition stops for any reason
}

export interface SpeechInputAdapter {
  readonly name: string;
  readonly supported: boolean;
  /** Where audio is processed, shown to the user before they speak. */
  readonly privacyNote: string;
  /** continuous: keep listening until stop() (push-to-talk); otherwise end after one utterance (wake phrase). */
  start(lang: string, handlers: RecognitionHandlers, opts?: { continuous?: boolean }): void;
  stop(): void; // finish and deliver the final transcript
  abort(): void; // stop without a transcript
}

export interface UtteranceHandlers {
  onStart?: () => void;
  onEnd?: () => void;
  onBoundary?: () => void; // a word boundary was reached while speaking (real playback progress)
  onError?: (message: string) => void;
}

export interface VoiceInfo {
  uri: string;
  name: string;
  lang: string;
  local: boolean; // synthesised on this device (false: the browser sends the text to an online voice service)
}

export interface SpeechOutputAdapter {
  readonly name: string;
  readonly supported: boolean;
  voiceURI: string | null; // chosen voice; null = best local voice for the language
  voices(): VoiceInfo[];
  speak(text: string, lang: string, handlers: UtteranceHandlers): void;
  cancel(): void; // stop immediately and drop anything queued
}
