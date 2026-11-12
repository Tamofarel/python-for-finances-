import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { setCsrfToken } from "../api/client";
import { useAssistant } from "../store/assistant";
import { useChat } from "../store/chat";
import { DEFAULT_PREFS, DEFAULT_VOICE, usePrefs } from "../store/prefs";
import { useVoice } from "../store/voice";
import { initialSnapshot } from "../assistant/machine";
import { StreamingSpeaker, speakable } from "../voice/speaker";
import type { RecognitionHandlers, SpeechInputAdapter, SpeechOutputAdapter, UtteranceHandlers } from "../voice/types";
import { useAssistantWiring } from "../assistant/wiring";
import { renderHook } from "@testing-library/react";
import { mockApi, now, sseResponse } from "./utils";

class FakeOutput implements SpeechOutputAdapter {
  name = "fake";
  supported = true;
  voiceURI: string | null = null;
  voices() {
    return [];
  }
  spoken: string[] = [];
  pending: UtteranceHandlers[] = [];
  cancelled = 0;
  speak(text: string, _lang: string, h: UtteranceHandlers) {
    this.spoken.push(text);
    this.pending.push(h);
    h.onStart?.();
  }
  finishAll() {
    const p = this.pending;
    this.pending = [];
    p.forEach((h) => h.onEnd?.());
  }
  cancel() {
    this.cancelled += 1;
    this.pending = [];
  }
}

class FakeInput implements SpeechInputAdapter {
  name = "fake";
  supported = true;
  privacyNote = "test";
  handlers: RecognitionHandlers | null = null;
  start(_lang: string, h: RecognitionHandlers) {
    this.handlers = h;
  }
  stop() {
    this.handlers?.onEnd();
  }
  abort() {
    this.handlers?.onEnd();
  }
  say(text: string) {
    this.handlers?.onInterim(text);
    this.handlers?.onFinal(text);
    this.handlers?.onEnd();
  }
}

describe("speakable text", () => {
  it("removes markdown, links and code", () => {
    expect(speakable("## Title\n- **Bold** [link](https://x.y) and `code`\n```js\nx()\n```")).toBe("Title Bold link and code");
  });
});

describe("StreamingSpeaker", () => {
  it("speaks complete sentences as they stream and skips code blocks split across deltas", () => {
    const out = new FakeOutput();
    const events: string[] = [];
    const sp = new StreamingSpeaker(out, "en", {
      onSpeakStart: () => events.push("start"), onSpeakEnd: () => events.push("end"), onBoundary: () => {}, onError: () => {},
    });
    sp.push("Server-sent events keep one HTTP response");
    expect(out.spoken).toEqual([]);
    sp.push(" open. Here is code:\n`");
    sp.push("``python\nprint('secret code')\n``");
    sp.push("`\nThat is all for now.");
    sp.finish();
    expect(out.spoken[0]).toBe("Server-sent events keep one HTTP response open.");
    expect(out.spoken).toContain("I've put the code on screen.");
    expect(out.spoken.join(" ")).not.toContain("secret code");
    expect(out.spoken.at(-1)).toBe("That is all for now.");
    expect(events).toEqual(["start"]);
    out.finishAll();
    expect(events).toEqual(["start", "end"]);
  });

  it("stops immediately when cancelled", () => {
    const out = new FakeOutput();
    let ended = false;
    const sp = new StreamingSpeaker(out, "en", { onSpeakStart: () => {}, onSpeakEnd: () => { ended = true; }, onBoundary: () => {}, onError: () => {} });
    sp.push("A first complete sentence here. And a second one is coming.");
    sp.cancel();
    sp.push("More text that must never be spoken.");
    sp.finish();
    expect(out.cancelled).toBe(1);
    expect(out.spoken.join(" ")).not.toContain("never be spoken");
    expect(ended).toBe(false);
  });
});

describe("voice conversation", () => {
  let input: FakeInput;
  let output: FakeOutput;
  beforeEach(() => {
    setCsrfToken("csrf");
    input = new FakeInput();
    output = new FakeOutput();
    usePrefs.setState({ prefs: { ...DEFAULT_PREFS, voice: { ...DEFAULT_VOICE, speak_replies: true, acknowledge: false, lang: "en-US" } }, synced: false });
    useVoice.setState({ input, output, listening: false, listenVia: null, speaking: false, error: null, mic: "off", meter: null });
    useChat.setState({ current: null, conversations: [], streaming: false, streamError: null, controller: null });
    useAssistant.setState({ snapshot: initialSnapshot, state: "standby", history: [] });
  });

  const conv = { id: 3, title: "New conversation", project_id: null, provider: null, model: null, created_at: now, updated_at: now };
  const user = { id: 1, role: "user", content: "what is SSE", status: "complete", error: null, provider: null, model: null, created_at: now };

  it("sends the real transcript into the conversation pipeline and speaks the model output", async () => {
    const { calls } = mockApi({
      "POST /api/conversations": () => conv,
      "GET /api/conversations": () => [conv],
      "GET /api/actions": () => [], "GET /api/tasks": () => [], "GET /api/workflows": () => [],
      "POST /api/conversations/3/messages": () => sseResponse([
        ["start", { assistant_message_id: 4, provider: "ollama", model: "m", user_message: user }],
        ["delta", { text: "SSE streams events over one response. " }],
        ["delta", { text: "It is one-way." }],
        ["done", { message: { ...user, id: 4, role: "assistant", content: "SSE streams events over one response. It is one-way." } }],
      ]),
    });
    renderHook(() => useAssistantWiring(60_000));
    await useVoice.getState().startListening("chat");
    expect(useAssistant.getState().state).toBe("listening");
    input.say("what is SSE");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/conversations/3/messages")).toBe(true));
    expect(calls.find((c) => c.url === "/api/conversations/3/messages")!.body).toMatchObject({ content: "what is SSE" });
    await waitFor(() => expect(output.spoken).toEqual(["SSE streams events over one response.", "It is one-way."]));
    expect(useAssistant.getState().state).toBe("speaking");
    output.finishAll();
    await waitFor(() => expect(useAssistant.getState().state).toBe("standby"));
  });

  it("routes spoken actions through the assistant and speaks only the verified result", async () => {
    const result = {
      id: "0-c1", phase: "finished", tool: "spotify_control", risk: "reversible",
      arguments: { action: "play_track", query: "Vie de Pirate", artist: "Tiakola" },
      status: "succeeded", message: "Playing Vie de Pirate by Tiakola on Spotify (PC).", verified: true,
      data: { verified: true }, duration_ms: 900,
    };
    const { calls } = mockApi({
      "POST /api/conversations": () => conv,
      "GET /api/conversations": () => [conv],
      "GET /api/actions": () => [], "GET /api/tasks": () => [], "GET /api/workflows": () => [],
      "POST /api/conversations/3/messages": () => sseResponse([
        ["start", { assistant_message_id: 4, provider: "ollama", model: "m", user_message: user }],
        ["action", { ...result, phase: "started" }],
        ["action", result],
        ["delta", { text: "Your song is playing now, enjoy!" }],
        ["done", { message: { ...user, id: 4, role: "assistant", content: "Your song is playing now, enjoy!", actions: [result] } }],
      ]),
    });
    renderHook(() => useAssistantWiring(60_000));
    await useVoice.getState().startListening("chat");
    input.say("Hey NOVA, open Spotify and play Tiakola Vie de Pirate");
    await waitFor(() => expect(output.spoken).toEqual(["Playing Vie de Pirate by Tiakola on Spotify (PC)."]));
    // One pipeline: the transcript goes to the assistant, never to a client-side shortcut.
    expect(calls.find((c) => c.url === "/api/conversations/3/messages")!.body).toMatchObject({
      content: "Hey NOVA, open Spotify and play Tiakola Vie de Pirate" });
    expect(calls.some((c) => c.url === "/api/tools/execute")).toBe(false);
  });

  it("interrupts speech and the running generation immediately", async () => {
    let release: (() => void) | null = null;
    mockApi({
      "POST /api/conversations": () => conv,
      "GET /api/conversations": () => [conv],
      "POST /api/conversations/3/messages": () => {
        const body = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(
              `event: start\ndata: ${JSON.stringify({ assistant_message_id: 4, provider: "p", model: "m", user_message: user })}\n\n` +
              `event: delta\ndata: ${JSON.stringify({ text: "This sentence is spoken right away. " })}\n\n`));
            release = () => controller.close();
          },
        });
        return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
      },
    });
    renderHook(() => useAssistantWiring(60_000));
    await useVoice.getState().startListening("chat");
    input.say("hello");
    await waitFor(() => expect(output.spoken.length).toBe(1));
    expect(useVoice.getState().speaking).toBe(true);
    useVoice.getState().interrupt();
    expect(output.cancelled).toBeGreaterThan(0);
    expect(useVoice.getState().speaking).toBe(false);
    (release as (() => void) | null)?.();
    await waitFor(() => expect(useChat.getState().streaming).toBe(false));
    expect(useChat.getState().current?.messages.at(-1)?.status).toBe("cancelled");
    expect(useAssistant.getState().state).toBe("cancelled");
  });

  it("does not read typed messages aloud", async () => {
    mockApi({
      "POST /api/conversations": () => conv,
      "GET /api/conversations": () => [conv],
      "POST /api/conversations/3/messages": () => sseResponse([
        ["start", { assistant_message_id: 4, provider: "p", model: "m", user_message: user }],
        ["delta", { text: "A typed question gets a written answer. " }],
        ["done", { message: { ...user, id: 4, role: "assistant", content: "A typed question gets a written answer." } }],
      ]),
    });
    await useChat.getState().send("typed question");
    expect(output.spoken).toEqual([]);
  });

  it("says a short acknowledgement without pretending to speak the answer", async () => {
    usePrefs.setState({ prefs: { ...usePrefs.getState().prefs, voice: { ...usePrefs.getState().prefs.voice, acknowledge: true } } });
    mockApi({ "POST /api/conversations": () => conv, "GET /api/conversations": () => [conv],
      "POST /api/conversations/3/messages": () => new Response("", { headers: { "Content-Type": "text/event-stream" } }) });
    renderHook(() => useAssistantWiring(60_000));
    await useVoice.getState().startListening("chat");
    input.say("hello");
    expect(output.spoken[0]).toBe("Okay.");
    expect(useVoice.getState().speaking).toBe(false); // the cue does not change NOVA's state
    await waitFor(() => expect(["understanding", "thinking", "standby"]).toContain(useAssistant.getState().state));
    expect(useAssistant.getState().history.some((h) => h.event === "SPEAK_START")).toBe(false);
  });

  it("puts a transcript into a field without sending when targeted", async () => {
    const { calls } = mockApi({});
    let got = "";
    await useVoice.getState().startListening((t) => { got = t; });
    input.say("tomatoes for home gardeners");
    expect(got).toBe("tomatoes for home gardeners");
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("explains when voice input is unsupported", async () => {
    useVoice.setState({ input: Object.assign(new FakeInput(), { supported: false }) });
    await useVoice.getState().startListening("chat");
    expect(useVoice.getState().error).toMatch(/no speech recognition/);
    expect(useVoice.getState().listening).toBe(false);
  });
});
