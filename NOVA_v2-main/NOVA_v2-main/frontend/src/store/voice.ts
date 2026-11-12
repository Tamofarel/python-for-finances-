// Voice controller: wake phrase, push-to-talk, transcripts, spoken replies, barge-in and follow-up listening.
//
// - The wake phrase is detected by NOVA's backend on this computer (PocketSphinx). While the wake phrase is on, the
//   microphone audio goes to the local backend only; nothing is stored.
// - After the wake phrase (or while Talk is held) speech is transcribed either by the browser (Chrome and Edge send
//   that audio to their vendor's speech service) or by a local engine on the backend. The owner chooses.
// - Transcripts go into the existing conversation pipeline (useChat.send) or to a handler registered by the screen
//   that is open (for example the idea workflow); there is no second engine.

import { create } from "zustand";
import { api } from "../api/client";
import type { VoiceCapabilities, VoicePrefs } from "../api/types";
import { BrowserSpeechInput, BrowserSpeechOutput } from "../voice/browser";
import { MicCapture, MicError } from "../voice/capture";
import { LocalVoiceStream, playChime, type ServerEvent } from "../voice/localStream";
import { MicLevel, type MicPermission, microphonePermission } from "../voice/mic";
import { StreamingSpeaker, speakable } from "../voice/speaker";
import type { SpeechInputAdapter, SpeechOutputAdapter } from "../voice/types";
import { dispatchAssistant } from "./assistant";
import { onChatStream, useChat } from "./chat";
import { usePrefs } from "./prefs";

/** off: microphone closed · starting · armed: listening for the wake phrase on this computer ·
 *  suspended: waiting for a click before the browser lets audio processing start · denied / error: needs attention. */
export type MicState = "off" | "starting" | "armed" | "suspended" | "denied" | "error";

export type TranscriptTarget = "chat" | ((text: string) => void);
export type TranscriptHandler = (text: string) => void;

interface VoiceState {
  input: SpeechInputAdapter;
  output: SpeechOutputAdapter;
  capabilities: VoiceCapabilities | null;
  mic: MicState;
  micMessage: string | null;
  listening: boolean; // an utterance is being captured right now
  listenVia: "browser" | "local" | null;
  interim: string;
  lastTranscript: string;
  error: string | null;
  speaking: boolean;
  micPermission: MicPermission;
  meter: MicLevel | null; // level meter during browser push-to-talk when the wake microphone is closed
  boundaryPulse: number; // increments on every real word boundary during speech output
  loadCapabilities: () => Promise<void>;
  refreshPermission: () => Promise<void>;
  /** Turn the wake phrase on. The first time, `consent` must be true (recorded on the server). */
  enableWake: (opts?: { consent?: boolean; stt?: VoicePrefs["stt_engine"] }) => Promise<boolean>;
  /** Microphone off: stop listening for the wake phrase and close the microphone. */
  micOff: () => Promise<void>;
  /** Close everything without changing preferences (sign-out, page hidden for good). */
  release: () => void;
  retryMic: () => Promise<void>;
  /** Re-arm a previously enabled wake listener when the signed-in app opens or returns to the foreground. */
  resumeWake: () => Promise<void>;
  startListening: (target?: TranscriptTarget) => Promise<void>;
  stopListening: () => void;
  cancelListening: () => void;
  interrupt: () => void; // stop speech and any generation, immediately
  stopSpeaking: () => void;
  /** Speak a message from NOVA (e.g. a workflow question). followUp: listen for the answer afterwards. */
  speak: (text: string, opts?: { followUp?: boolean }) => void;
  setTranscriptHandler: (fn: TranscriptHandler | null) => () => void;
  level: () => number;
  samples: () => Uint8Array | null;
  clearError: () => void;
}

let speaker: StreamingSpeaker | null = null;
let target: TranscriptTarget = "chat";
let handler: TranscriptHandler | null = null;
let capture: MicCapture | null = null; // microphone feeding the local voice stream
let stream: LocalVoiceStream | null = null;
let streamMode: "wake" | "utterance" | null = null;
let closingByUs = false;
let reconnects = 0;
let voiceTurnAt = 0; // when a voice transcript was last delivered (the next request within a few seconds is "voice")
const VOICE_TURN_MS = 15_000;
let followUpPending = false;
let replyStreamDone = false;
let actionTurn = false; // the reply being spoken ran tools: their results are spoken instead of the model's text
let gestureCleanup: (() => void) | null = null;
let speechPlaybackStartedAt: number | null = null;

function logVoiceTiming(stage: string, durationMs: number): void {
  if (Number.isFinite(durationMs)) console.info("NOVA voice timing", { stage, duration_ms: Math.round(durationMs) });
}

function voiceNow(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

const voicePrefs = (): VoicePrefs => usePrefs.getState().prefs.voice;

export const useVoice = create<VoiceState>((set, get) => {
  // ---------------------------------------------------------------------------------------------------------
  // Spoken replies follow the one conversation pipeline.
  function newSpeaker(): StreamingSpeaker {
    const { output } = get();
    const speechQueuedAt = voiceNow();
    return new StreamingSpeaker(output, voicePrefs().lang, {
      onSpeakStart: () => {
        speechPlaybackStartedAt = voiceNow();
        logVoiceTiming("tts_startup", speechPlaybackStartedAt - speechQueuedAt);
        set({ speaking: true });
        dispatchAssistant({ type: "SPEAK_START" });
      },
      onSpeakEnd: () => {
        if (speechPlaybackStartedAt !== null) {
          logVoiceTiming("tts_playback", voiceNow() - speechPlaybackStartedAt);
          speechPlaybackStartedAt = null;
        }
        set({ speaking: false });
        dispatchAssistant({ type: "SPEAK_END" });
        maybeFollowUp();
      },
      onBoundary: () => set({ boundaryPulse: get().boundaryPulse + 1 }),
      onError: (message) => set({ error: message }),
    });
  }

  onChatStream((e) => {
    const { output } = get();
    if (e.type === "send") {
      if (speaker?.active) speaker.cancel();
      const fromVoice = Date.now() - voiceTurnAt < VOICE_TURN_MS;
      voiceTurnAt = 0;
      speaker = fromVoice && voicePrefs().speak_replies && output.supported ? newSpeaker() : null;
      followUpPending = fromVoice;
      replyStreamDone = false;
      actionTurn = false;
    } else if (e.type === "action") {
      // When NOVA acted, speak the tool's own result (verified or honestly not), not the model's paraphrase of it.
      if (e.action.phase === "finished" && e.action.message && speaker) {
        actionTurn = true;
        speaker.push(`${e.action.message} `);
      }
    } else if (e.type === "delta") {
      if (!actionTurn) speaker?.push(e.text);
    } else if (e.type === "end") {
      if (e.outcome === "done") {
        replyStreamDone = true;
        if (speaker) speaker.finish();
        maybeFollowUp();
      } else {
        followUpPending = false;
        replyStreamDone = false;
        speaker?.cancel();
        speaker = null;
        if (get().speaking) {
          set({ speaking: false });
          dispatchAssistant({ type: "SPEAK_INTERRUPT" });
        }
      }
    }
  });

  /** After a voice request and its spoken reply have both finished, listen once without the wake phrase. */
  function maybeFollowUp(): void {
    if (!followUpPending || !replyStreamDone || speaker?.active || get().speaking) return;
    followUpPending = false;
    if (!voicePrefs().follow_up || get().mic !== "armed" || get().listening) return;
    beginUtterance("wake-follow-up");
  }

  // ---------------------------------------------------------------------------------------------------------
  // Transcript routing
  function deliver(text: string): void {
    set({ lastTranscript: text, interim: text });
    const t = target;
    target = "chat";
    if (typeof t === "function") {
      t(text);
      dispatchAssistant({ type: "TRANSCRIPT_DONE" });
      return;
    }
    // Spoken requests to act ("open Spotify and play …") take the same path as typed ones: the assistant chooses a
    // registered tool, NOVA's backend validates and runs it, and the verified result is spoken (see onChatStream).
    voiceTurnAt = Date.now();
    if (voicePrefs().acknowledge && voicePrefs().speak_replies) speakCue("Okay.");
    if (handler) {
      dispatchAssistant({ type: "TRANSCRIPT_DONE" });
      handler(text);
      return;
    }
    void useChat.getState().send(text); // SEND reaches the state machine through the chat stream wiring
  }

  /** A short spoken cue (like the chime): it does not change the assistant state, which keeps showing real work. */
  function speakCue(text: string): void {
    const { output } = get();
    if (output.supported) output.speak(text, voicePrefs().lang, {});
  }

  function fail(message: string): void {
    set({ error: message });
    dispatchAssistant({ type: "VOICE_ERROR", message });
  }

  // ---------------------------------------------------------------------------------------------------------
  // Browser recognition (Web Speech API)
  function browserListen(continuous: boolean, onDone?: () => void): void {
    const { input } = get();
    if (!input.supported) {
      fail("This browser has no speech recognition. Choose a local engine in Settings → Voice, or type instead.");
      onDone?.();
      return;
    }
    let gotFinal = false;
    const recognitionStartedAt = voiceNow();
    set({ listening: true, listenVia: "browser", interim: "", error: null });
    input.start(voicePrefs().lang, {
      onStart: () => dispatchAssistant({ type: "LISTEN_START" }),
      onInterim: (text) => set({ interim: text }),
      onFinal: (text) => {
        gotFinal = true;
        logVoiceTiming("stt_browser", voiceNow() - recognitionStartedAt);
        deliver(text);
      },
      onError: (message) => fail(message),
      onEnd: () => {
        logVoiceTiming("endpoint_browser", voiceNow() - recognitionStartedAt);
        get().meter?.close();
        set({ listening: false, listenVia: null, meter: null });
        if (!gotFinal && !get().error) dispatchAssistant({ type: "TRANSCRIPT_EMPTY" });
        void get().refreshPermission();
        onDone?.();
      },
    }, { continuous });
  }

  // ---------------------------------------------------------------------------------------------------------
  // Local stream (wake phrase and local transcription)
  function onServerEvent(ev: ServerEvent): void {
    switch (ev.type) {
      case "wake":
        // Barge-in: saying the wake phrase over NOVA stops its speech and the reply being generated.
        if (get().speaking || useChat.getState().streaming) get().interrupt();
        if (voicePrefs().chime) playChime();
        dispatchAssistant({ type: "WAKE" });
        if (voicePrefs().stt_engine === "browser") {
          // The server paused itself; the browser recognises one utterance, then the detector is re-armed.
          browserListen(false, () => stream?.control("resume"));
        }
        break;
      case "listening":
        set({ listening: true, listenVia: "local", interim: "", error: null });
        dispatchAssistant({ type: "LISTEN_START" });
        break;
      case "partial":
        set({ interim: ev.text });
        break;
      case "transcribing":
        dispatchAssistant({ type: "LISTEN_END" });
        break;
      case "final":
        set({ listening: false, listenVia: null });
        deliver(ev.text);
        if (streamMode === "utterance") closeStream();
        break;
      case "empty":
        set({ listening: false, listenVia: null });
        target = "chat";
        dispatchAssistant({ type: "TRANSCRIPT_EMPTY" });
        if (streamMode === "utterance") closeStream();
        break;
      case "error":
        set({ listening: false, listenVia: null });
        fail(ev.message);
        if (streamMode === "utterance") closeStream();
        break;
      default:
        break;
    }
  }

  function closeStream(): void {
    closingByUs = true;
    stream?.close();
    capture?.stop();
    stream = null;
    capture = null;
    streamMode = null;
    gestureCleanup?.();
    gestureCleanup = null;
    closingByUs = false;
  }

  function waitForGesture(): void {
    gestureCleanup?.();
    const resume = () => {
      void capture?.resume().then((ok) => {
        if (ok) {
          gestureCleanup?.();
          gestureCleanup = null;
          if (get().mic === "suspended") set({ mic: "armed", micMessage: null });
        }
      });
    };
    window.addEventListener("pointerdown", resume);
    window.addEventListener("keydown", resume);
    gestureCleanup = () => {
      window.removeEventListener("pointerdown", resume);
      window.removeEventListener("keydown", resume);
    };
  }

  async function openStream(mode: "wake" | "utterance"): Promise<boolean> {
    const v = voicePrefs();
    const mine = new LocalVoiceStream(
      { mode, stt: v.stt_engine, phrase: v.wake_phrase, lang: v.lang, sensitivity: v.sensitivity },
      onServerEvent,
      (reason) => {
        if (stream !== mine || closingByUs) return;
        const wasWake = streamMode === "wake";
        capture?.stop();
        stream = null;
        capture = null;
        streamMode = null;
        set({ listening: false, listenVia: null });
        if (wasWake && voicePrefs().wake_enabled && reconnects < 3 && get().mic === "armed") {
          reconnects += 1;
          set({ mic: "starting", micMessage: "Reconnecting to NOVA's voice service…" });
          setTimeout(() => void arm(), 1500 * reconnects);
        } else if (wasWake) {
          set({ mic: "error", micMessage: `The voice service stopped (${reason}). Try again when NOVA's backend is running.` });
          dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: false } });
        }
      },
    );
    // Registered before connecting so events that arrive right after "ready" can already be answered.
    stream = mine;
    streamMode = mode;
    const mic = new MicCapture();
    try {
      await mine.connect(); // the backend must accept the stream before the microphone opens
      await mic.start((chunk) => mine.sendAudio(chunk));
      if (stream !== mine) {
        mic.stop(); // closed while starting
        return false;
      }
      capture = mic;
      return true;
    } catch (e) {
      mic.stop();
      if (stream === mine) {
        closingByUs = true;
        mine.close();
        closingByUs = false;
        stream = null;
        streamMode = null;
      }
      const denied = e instanceof MicError && e.kind === "denied";
      set({ mic: denied ? "denied" : "error", micMessage: (e as Error).message });
      void get().refreshPermission();
      return false;
    }
  }

  /** Open the microphone and the local wake-phrase detector (only when the owner turned the wake phrase on). */
  let arming = false;
  async function arm(): Promise<void> {
    if (!voicePrefs().wake_enabled || streamMode === "wake" || arming) return;
    arming = true;
    try {
      if (stream) closeStream(); // a push-to-talk stream gives way to the wake stream
      set({ mic: "starting", micMessage: null });
      if (!(await openStream("wake"))) {
        dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: false } });
        return;
      }
      if (!voicePrefs().wake_enabled) {
        closeStream(); // turned off while starting
        set({ mic: "off" });
        return;
      }
      reconnects = 0;
      if (capture?.suspended) {
        set({ mic: "suspended", micMessage: "Click anywhere or press a key so the browser lets NOVA listen." });
        waitForGesture();
      } else set({ mic: "armed", micMessage: null });
      dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: true } });
    } finally {
      arming = false;
    }
  }

  /** Capture one utterance through the open wake stream (follow-up) — no wake phrase needed. */
  function beginUtterance(_reason: "wake-follow-up"): void {
    if (voicePrefs().stt_engine === "browser") {
      stream?.control("pause");
      browserListen(false, () => stream?.control("resume"));
    } else stream?.control("listen", { ptt: false });
  }

  // Re-arm when the relevant preferences change (phrase, sensitivity, engine, on/off).
  let lastKey = "";
  usePrefs.subscribe((s) => {
    if (!s.synced) return;
    const v = s.prefs.voice;
    get().output.voiceURI = v.tts_voice;
    const key = v.wake_enabled ? `${v.wake_phrase}|${v.sensitivity}|${v.stt_engine}` : "off";
    if (key === lastKey) return;
    lastKey = key;
    if (!v.wake_enabled) {
      if (streamMode === "wake") closeStream();
      set({ mic: "off", micMessage: null, listening: get().listenVia === "browser" ? get().listening : false });
      dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: false } });
      return;
    }
    if (streamMode === "wake") closeStream();
    void arm();
  });

  // ---------------------------------------------------------------------------------------------------------
  return {
    input: new BrowserSpeechInput(),
    output: new BrowserSpeechOutput(),
    capabilities: null,
    mic: "off",
    micMessage: null,
    listening: false,
    listenVia: null,
    interim: "",
    lastTranscript: "",
    error: null,
    speaking: false,
    micPermission: "unknown",
    meter: null,
    boundaryPulse: 0,

    loadCapabilities: async () => {
      try {
        set({ capabilities: await api.voice.capabilities() });
      } catch {
        /* keep the last known capabilities */
      }
    },

    refreshPermission: async () => set({ micPermission: await microphonePermission() }),

    enableWake: async (opts = {}) => {
      try {
        await usePrefs.getState().update({
          voice: { wake_enabled: true, ...(opts.stt ? { stt_engine: opts.stt } : {}) },
          ...(opts.consent ? { wake_consent: true } : {}),
        });
        return true;
      } catch (e) {
        set({ mic: "error", micMessage: (e as Error).message });
        return false;
      }
    },

    micOff: async () => {
      get().cancelListening();
      closeStream();
      set({ mic: "off", micMessage: null });
      dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: false } });
      if (voicePrefs().wake_enabled) {
        try {
          await usePrefs.getState().setVoice({ wake_enabled: false });
        } catch {
          /* the microphone is closed either way */
        }
      }
    },

    release: () => {
      get().interrupt();
      get().cancelListening();
      closeStream();
      lastKey = "";
      set({ mic: "off", micMessage: null });
      dispatchAssistant({ type: "CONTEXT", patch: { wakeArmed: false } });
    },

    retryMic: async () => {
      closeStream();
      reconnects = 0;
      await get().refreshPermission();
      await arm();
    },

    resumeWake: async () => {
      if (!voicePrefs().wake_enabled) return;
      await arm();
    },

    startListening: async (t = "chat") => {
      if (get().listening) return;
      // Barge-in: talking over NOVA stops its speech and the reply being generated.
      if (get().speaking || useChat.getState().streaming) get().interrupt();
      target = t;
      set({ error: null, interim: "" });
      dispatchAssistant({ type: "PTT_START" });
      const engine = voicePrefs().stt_engine;
      if (engine === "browser") {
        const armed = streamMode === "wake";
        if (armed) stream?.control("pause");
        browserListen(true, () => armed && stream?.control("resume"));
        if (!armed && get().listening) {
          // Waveform: a level meter on the same microphone, only while Talk is active.
          const meter = new MicLevel();
          if (await meter.open()) {
            if (get().listening) set({ meter });
            else meter.close();
          }
        }
        return;
      }
      // Local transcription on NOVA's backend.
      set({ listening: true, listenVia: "local" });
      if (streamMode === "wake") {
        stream?.control("listen", { ptt: true });
        return;
      }
      if (!(await openStream("utterance"))) {
        set({ listening: false, listenVia: null });
        fail(get().micMessage ?? "The microphone could not be started.");
        set({ mic: "off" });
      }
    },

    stopListening: () => {
      if (!get().listening) return;
      if (get().listenVia === "browser") {
        dispatchAssistant({ type: "LISTEN_END" });
        get().input.stop();
      } else stream?.control("end");
    },

    cancelListening: () => {
      if (!get().listening) return;
      target = "chat";
      if (get().listenVia === "browser") get().input.abort();
      else if (streamMode === "utterance") {
        closeStream();
        dispatchAssistant({ type: "TRANSCRIPT_EMPTY" });
      } else stream?.control("cancel");
      set({ listening: false, listenVia: null });
    },

    interrupt: () => {
      const wasSpeaking = get().speaking;
      followUpPending = false;
      speaker?.cancel();
      speaker = null;
      get().output.cancel();
      if (get().listenVia === "browser") get().input.abort();
      set({ speaking: false });
      if (useChat.getState().streaming) useChat.getState().cancel(); // ABORTED reaches the state machine
      else if (wasSpeaking) dispatchAssistant({ type: "SPEAK_INTERRUPT" });
    },

    stopSpeaking: () => {
      if (!get().speaking && !speaker) return;
      followUpPending = false;
      speaker?.cancel();
      speaker = null;
      get().output.cancel();
      if (get().speaking) {
        set({ speaking: false });
        dispatchAssistant({ type: "SPEAK_INTERRUPT" });
      }
    },

    speak: (text, opts = {}) => {
      const { output } = get();
      if (!output.supported) return;
      if (opts.followUp) followUpPending = true;
      output.speak(speakable(text), voicePrefs().lang, {
        onStart: () => {
          set({ speaking: true });
          dispatchAssistant({ type: "SPEAK_START" });
        },
        onBoundary: () => set({ boundaryPulse: get().boundaryPulse + 1 }),
        onEnd: () => {
          if (speaker?.active) return; // the streaming reply continues speaking
          if (get().speaking) {
            set({ speaking: false });
            dispatchAssistant({ type: "SPEAK_END" });
          }
          if (opts.followUp) maybeFollowUp();
        },
      });
    },

    setTranscriptHandler: (fn) => {
      handler = fn;
      return () => {
        if (handler === fn) handler = null;
      };
    },

    level: () => capture?.level() ?? get().meter?.level() ?? 0,
    samples: () => capture?.samples() ?? get().meter?.samples() ?? null,

    clearError: () => set({ error: null }),
  };
});

/** The level of the open microphone (0..1), for the core and waveform. */
export const micLevelSource = (): number => useVoice.getState().level();
