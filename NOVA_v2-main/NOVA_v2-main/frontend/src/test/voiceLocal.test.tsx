// The local wake-phrase path: microphone → NOVA backend (WebSocket) → events → state machine → conversation.
// The microphone and the socket are fakes; the protocol and store logic are the real code.

import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setCsrfToken } from "../api/client";
import { initialSnapshot } from "../assistant/machine";
import { useAssistant } from "../store/assistant";
import { useChat } from "../store/chat";
import { DEFAULT_PREFS, DEFAULT_VOICE, usePrefs } from "../store/prefs";
import { useVoice } from "../store/voice";
import { MicError } from "../voice/capture";
import { mockApi, now } from "./utils";

const mic = vi.hoisted(() => ({ failNext: null as null | Error, chunks: null as null | ((b: ArrayBuffer) => void), stopped: 0 }));

vi.mock("../voice/capture", async (orig) => {
  const real = await orig<typeof import("../voice/capture")>();
  class FakeCapture {
    suspended = false;
    async start(onPcm: (b: ArrayBuffer) => void) {
      if (mic.failNext) {
        const e = mic.failNext;
        mic.failNext = null;
        throw e;
      }
      mic.chunks = onPcm;
    }
    level() { return 0.2; }
    samples() { return null; }
    async resume() { return true; }
    stop() { mic.stopped += 1; mic.chunks = null; }
  }
  return { ...real, MicCapture: FakeCapture };
});

class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
  readyState = 0;
  binaryType = "blob";
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((e: { reason: string }) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }
  send(d: string | ArrayBuffer) { this.sent.push(d); }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({ reason: "closed" });
  }
  server(ev: object) { this.onmessage?.({ data: JSON.stringify(ev) }); }
  json(): Record<string, unknown>[] { return this.sent.filter((x): x is string => typeof x === "string").map((x) => JSON.parse(x)); }
}

const conv = { id: 3, title: "New conversation", project_id: null, provider: null, model: null, created_at: now, updated_at: now };

function enableWake(stt: "pocketsphinx" | "browser" = "pocketsphinx") {
  usePrefs.setState({
    prefs: { ...DEFAULT_PREFS, wake_consent_at: now, voice: { ...DEFAULT_VOICE, wake_enabled: true, stt_engine: stt, chime: false, acknowledge: false, speak_replies: false } },
    synced: true,
  });
}

beforeEach(() => {
  setCsrfToken("csrf-xyz");
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  mic.failNext = null;
  mic.stopped = 0;
  useChat.setState({ current: null, conversations: [], streaming: false, streamError: null, controller: null });
  useAssistant.setState({ snapshot: initialSnapshot, state: "standby", history: [] });
  usePrefs.setState({ prefs: DEFAULT_PREFS, synced: false });
});

afterEach(() => {
  useVoice.getState().release();
  usePrefs.setState({ prefs: DEFAULT_PREFS, synced: false });
  vi.unstubAllGlobals();
});

describe("local wake phrase", () => {
  it("re-arms the saved hands-free listener after the app reconnects", async () => {
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    FakeSocket.last!.server({ type: "ready", mode: "wake" });
    await waitFor(() => expect(useVoice.getState().mic).toBe("armed"));
    const first = FakeSocket.last;

    useVoice.getState().release();
    const resuming = useVoice.getState().resumeWake();
    await waitFor(() => expect(FakeSocket.last).not.toBe(first));
    FakeSocket.last!.server({ type: "ready", mode: "wake" });
    await resuming;
    expect(FakeSocket.last!.json()[0]).toMatchObject({ mode: "wake", phrase: "hey nova" });
  });

  it("connects with the CSRF token, arms the detector and turns a spoken request into a message", async () => {
    const { calls } = mockApi({
      "POST /api/conversations": () => conv, "GET /api/conversations": () => [conv],
      "POST /api/conversations/3/messages": () => new Response("", { headers: { "Content-Type": "text/event-stream" } }),
    });
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    const ws = FakeSocket.last!;
    expect(ws.url).toMatch(/\/api\/voice\/stream$/);
    expect(ws.json()[0]).toMatchObject({ type: "hello", csrf: "csrf-xyz", mode: "wake", stt: "pocketsphinx", phrase: "hey nova", sample_rate: 16000 });
    ws.server({ type: "ready", mode: "wake", phrase: "hey nova" });
    await waitFor(() => expect(useVoice.getState().mic).toBe("armed"));
    expect(useAssistant.getState().snapshot.context.wakeArmed).toBe(true);

    mic.chunks!(new Int16Array(1600).buffer); // microphone audio goes to the local backend only
    expect(ws.sent.some((x) => x instanceof ArrayBuffer)).toBe(true);

    ws.server({ type: "wake", phrase: "hey nova" });
    expect(useAssistant.getState().state).toBe("wake_detected");
    ws.server({ type: "listening" });
    expect(useAssistant.getState().state).toBe("listening");
    ws.server({ type: "partial", text: "what is the" });
    expect(useVoice.getState().interim).toBe("what is the");
    ws.server({ type: "transcribing" });
    expect(useAssistant.getState().state).toBe("transcribing");
    ws.server({ type: "final", text: "what is the weather like today" });
    await waitFor(() => expect(calls.find((c) => c.url === "/api/conversations/3/messages")?.body).toMatchObject({ content: "what is the weather like today" }));
    expect(useVoice.getState().mic).toBe("armed"); // back to listening for the wake phrase
  });

  it("lets the wake phrase interrupt NOVA while it is speaking (barge-in)", async () => {
    let cancelled = 0;
    useVoice.setState({ output: { name: "fake", supported: true, voiceURI: null, voices: () => [], speak: () => {}, cancel: () => { cancelled += 1; } } as never });
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    const ws = FakeSocket.last!;
    ws.server({ type: "ready", mode: "wake" });
    useVoice.setState({ speaking: true });
    useAssistant.getState().dispatch({ type: "SPEAK_START" });
    ws.server({ type: "wake", phrase: "hey nova" });
    expect(cancelled).toBeGreaterThan(0);
    expect(useVoice.getState().speaking).toBe(false);
    expect(useAssistant.getState().state).toBe("wake_detected");
  });

  it("returns to standby when nothing is said after the wake phrase", async () => {
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    const ws = FakeSocket.last!;
    ws.server({ type: "ready", mode: "wake" });
    ws.server({ type: "wake", phrase: "hey nova" });
    ws.server({ type: "listening" });
    ws.server({ type: "empty", reason: "no_speech" });
    expect(useAssistant.getState().state).toBe("standby");
  });

  it("hands over to the browser recogniser after the wake phrase and re-arms the detector afterwards", async () => {
    mockApi({});
    const handlers: { onEnd?: () => void; onStart?: () => void } = {};
    useVoice.setState({ input: { name: "fake", supported: true, privacyNote: "", start: (_l: string, h: never) => Object.assign(handlers, h), stop() {}, abort() {} } as never });
    enableWake("browser");
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    const ws = FakeSocket.last!;
    ws.server({ type: "ready", mode: "wake" });
    ws.server({ type: "wake", phrase: "hey nova" });
    handlers.onStart?.();
    expect(useAssistant.getState().state).toBe("listening");
    handlers.onEnd?.(); // nothing recognised
    expect(ws.json().at(-1)).toEqual({ type: "resume" });
    expect(useAssistant.getState().state).toBe("standby");
  });

  it("turns the microphone off and saves the choice", async () => {
    const { calls } = mockApi({ "PUT /api/preferences": () => ({ ...usePrefs.getState().prefs, voice: { ...usePrefs.getState().prefs.voice, wake_enabled: false } }) });
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    FakeSocket.last!.server({ type: "ready", mode: "wake" });
    await waitFor(() => expect(useVoice.getState().mic).toBe("armed"));
    await useVoice.getState().micOff();
    expect(useVoice.getState().mic).toBe("off");
    expect(mic.stopped).toBeGreaterThan(0);
    expect(FakeSocket.last!.readyState).toBe(3);
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ voice: { wake_enabled: false } });
    expect(useAssistant.getState().snapshot.context.wakeArmed).toBe(false);
  });

  it("explains a blocked microphone and recovers when the owner tries again", async () => {
    mic.failNext = new MicError("denied", "Microphone access is blocked. Allow it for this site in the browser's address bar, then try again.");
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    FakeSocket.last!.server({ type: "ready", mode: "wake" });
    await waitFor(() => expect(useVoice.getState().mic).toBe("denied"));
    expect(FakeSocket.last!.readyState).toBe(3); // nothing stays open after a refusal
    const first = FakeSocket.last;
    expect(useVoice.getState().micMessage).toMatch(/blocked/);
    const retry = useVoice.getState().retryMic();
    await waitFor(() => expect(FakeSocket.last).not.toBe(first));
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    FakeSocket.last!.server({ type: "ready", mode: "wake" });
    await retry;
    await waitFor(() => expect(useVoice.getState().mic).toBe("armed"));
  });

  it("reports a refused connection instead of pretending to listen", async () => {
    enableWake();
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    FakeSocket.last!.server({ type: "error", code: "denied", message: "Microphone audio is only accepted from this computer." });
    await waitFor(() => expect(useVoice.getState().mic).toBe("error"));
    expect(useVoice.getState().micMessage).toMatch(/only accepted from this computer/);
    expect(useAssistant.getState().snapshot.context.wakeArmed).toBe(false);
  });
});

describe("local push-to-talk", () => {
  it("opens a one-utterance stream, ends it on release and closes the microphone afterwards", async () => {
    const { calls } = mockApi({});
    usePrefs.setState({ prefs: { ...DEFAULT_PREFS, voice: { ...DEFAULT_VOICE, stt_engine: "pocketsphinx", acknowledge: false } }, synced: true });
    let got = "";
    const starting = useVoice.getState().startListening((t) => { got = t; });
    await waitFor(() => expect(FakeSocket.last?.sent.length).toBeGreaterThan(0));
    const ws = FakeSocket.last!;
    expect(ws.json()[0]).toMatchObject({ mode: "utterance", stt: "pocketsphinx" });
    ws.server({ type: "ready", mode: "utterance" });
    ws.server({ type: "listening" });
    await starting;
    expect(useVoice.getState().listening).toBe(true);
    useVoice.getState().stopListening();
    expect(ws.json().at(-1)).toEqual({ type: "end" });
    ws.server({ type: "transcribing" });
    ws.server({ type: "final", text: "tomatoes and basil" });
    expect(got).toBe("tomatoes and basil");
    expect(ws.readyState).toBe(3);
    expect(mic.stopped).toBeGreaterThan(0);
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
