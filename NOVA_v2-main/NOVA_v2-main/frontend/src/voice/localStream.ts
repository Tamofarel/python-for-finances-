// Client for NOVA's local voice stream (/api/voice/stream). Audio goes to the NOVA backend on this computer only;
// the backend refuses audio from other machines unless the owner explicitly allows it.

import { getCsrfToken } from "../api/client";

export type ServerEvent =
  | { type: "ready"; mode: string; phrase?: string }
  | { type: "wake"; phrase: string }
  | { type: "listening" }
  | { type: "speech" }
  | { type: "partial"; text: string }
  | { type: "transcribing" }
  | { type: "final"; text: string }
  | { type: "empty"; reason: string }
  | { type: "error"; code: string; message: string };

export interface StreamOptions {
  mode: "wake" | "utterance";
  stt: "browser" | "pocketsphinx" | "vosk";
  lang: string;
  phrase: string;
  sensitivity: "low" | "medium" | "high";
}

export class LocalVoiceStream {
  private ws: WebSocket | null = null;
  private ready = false;

  constructor(private opts: StreamOptions, private onEvent: (e: ServerEvent) => void, private onClose: (reason: string) => void) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/api/voice/stream`);
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      let settled = false;
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: "hello", csrf: getCsrfToken() ?? "", sample_rate: 16000, ...this.opts }));
      };
      ws.onmessage = (m) => {
        let ev: ServerEvent;
        try {
          ev = JSON.parse(String(m.data)) as ServerEvent;
        } catch {
          return;
        }
        if (!settled) {
          settled = true;
          if (ev.type === "ready") {
            this.ready = true;
            resolve();
          } else {
            reject(new Error(ev.type === "error" ? ev.message : "The voice service did not start."));
            return;
          }
        }
        this.onEvent(ev);
      };
      ws.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error("Cannot connect to NOVA's local voice service."));
        }
      };
      ws.onclose = (e) => {
        this.ready = false;
        this.ws = null;
        if (!settled) {
          settled = true;
          reject(new Error(e.reason || "The voice service closed the connection."));
        }
        this.onClose(e.reason || "closed");
      };
    });
  }

  get open(): boolean {
    return this.ready && this.ws?.readyState === WebSocket.OPEN;
  }

  sendAudio(chunk: ArrayBuffer): void {
    if (this.open) this.ws!.send(chunk);
  }

  control(type: "pause" | "resume" | "listen" | "end" | "cancel", extra: Record<string, unknown> = {}): void {
    if (this.open) this.ws!.send(JSON.stringify({ type, ...extra }));
  }

  close(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "close" }));
    this.ws?.close();
    this.ws = null;
    this.ready = false;
  }
}

/** Short two-tone chime played when the wake phrase is heard (generated locally, no audio file). */
export function playChime(): void {
  try {
    const ctx = new AudioContext();
    const g = ctx.createGain();
    g.gain.value = 0.0001;
    g.connect(ctx.destination);
    const o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(660, ctx.currentTime);
    o.frequency.setValueAtTime(880, ctx.currentTime + 0.09);
    o.connect(g);
    g.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);
    o.start();
    o.stop(ctx.currentTime + 0.24);
    o.onended = () => void ctx.close();
  } catch {
    /* audio output unavailable: the visual state still changes */
  }
}
