// Speaks a streaming reply sentence by sentence as the model produces it, and can be interrupted at any time.

import type { SpeechOutputAdapter } from "./types";

export interface SpeakerEvents {
  onSpeakStart: () => void; // first utterance started
  onSpeakEnd: () => void; // everything queued has been spoken (not called after cancel)
  onBoundary: () => void;
  onError: (message: string) => void;
}

const SENTENCE_END = /([.!?…])(\s|$)|\n{2,}/;
const MIN_CHUNK = 24;

/** Turn Markdown into speakable prose: code is summarised, symbols and URLs are removed. */
export function speakable(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\|.*\|\s*$/gm, " ")
    .replace(/[*_~>#|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export class StreamingSpeaker {
  private buffer = "";
  private pending = "";
  private inCode = false;
  private codeAnnounced = false;
  private queued = 0;
  private started = false;
  private cancelled = false;
  private finished = false;

  constructor(private output: SpeechOutputAdapter, private lang: string, private events: SpeakerEvents) {}

  /** Feed a streamed text delta. Complete sentences are spoken immediately. */
  push(delta: string): void {
    if (this.cancelled) return;
    this.pending += delta;
    for (;;) {
      const fence = this.pending.indexOf("```");
      if (fence < 0) break;
      this.take(this.pending.slice(0, fence));
      this.pending = this.pending.slice(fence + 3);
      this.inCode = !this.inCode;
      if (this.inCode) {
        this.flushSentences(true);
        if (!this.codeAnnounced) {
          this.codeAnnounced = true;
          this.say("I've put the code on screen.");
        }
      }
    }
    // Keep trailing backticks: they may be the start of a fence split across deltas.
    const keep = /`{1,2}$/.exec(this.pending)?.[0].length ?? 0;
    this.take(this.pending.slice(0, this.pending.length - keep));
    this.pending = this.pending.slice(this.pending.length - keep);
    this.flushSentences(false);
  }

  private take(text: string): void {
    if (!this.inCode) this.buffer += text;
  }

  /** The stream ended: speak whatever is left. */
  finish(): void {
    if (this.cancelled) return;
    this.take(this.pending);
    this.pending = "";
    this.finished = true;
    this.flushSentences(true);
    if (this.queued === 0) this.complete();
  }

  /** Stop speaking immediately and drop everything queued. */
  cancel(): void {
    this.cancelled = true;
    this.buffer = "";
    this.output.cancel();
  }

  get active(): boolean {
    return !this.cancelled && (this.queued > 0 || !this.finished);
  }

  private flushSentences(all: boolean): void {
    for (;;) {
      const m = SENTENCE_END.exec(this.buffer);
      if (!m || m.index + m[0].length < MIN_CHUNK) break;
      const cut = m.index + m[0].length;
      this.say(this.buffer.slice(0, cut));
      this.buffer = this.buffer.slice(cut);
    }
    if (all && this.buffer.trim()) {
      this.say(this.buffer);
      this.buffer = "";
    }
  }

  private say(text: string): void {
    const clean = speakable(text);
    if (!clean || this.cancelled) return;
    this.queued += 1;
    this.output.speak(clean, this.lang, {
      onStart: () => {
        if (!this.started && !this.cancelled) {
          this.started = true;
          this.events.onSpeakStart();
        }
      },
      onBoundary: () => this.events.onBoundary(),
      onError: (msg) => this.events.onError(msg),
      onEnd: () => {
        this.queued -= 1;
        if (this.queued === 0 && this.finished) this.complete();
      },
    });
  }

  private complete(): void {
    if (!this.cancelled && this.started) this.events.onSpeakEnd();
    this.cancelled = true;
  }
}
