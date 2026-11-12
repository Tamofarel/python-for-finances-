import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AUTO_RETURN_MS,
  type AssistantEvent,
  STATE_LABELS,
  TRANSITIONS,
  ambientState,
  displayState,
  initialSnapshot,
  type MachineSnapshot,
  transition,
} from "../assistant/machine";
import { useAssistant } from "../store/assistant";

function run(events: AssistantEvent[], start: MachineSnapshot = initialSnapshot) {
  const states: string[] = [];
  let s = start;
  for (const e of events) {
    s = transition(s, e);
    states.push(displayState(s));
  }
  return { s, states };
}

describe("assistant state machine", () => {
  it("follows a full wake-phrase turn", () => {
    const { states } = run([
      { type: "WAKE" }, { type: "LISTEN_START" }, { type: "LISTEN_END" }, { type: "SEND" }, { type: "ACCEPTED" },
      { type: "FIRST_DELTA" }, { type: "SPEAK_START" }, { type: "STREAM_DONE" }, { type: "SPEAK_END" },
    ]);
    expect(states).toEqual(["wake_detected", "listening", "transcribing", "understanding", "thinking", "responding",
      "speaking", "speaking", "standby"]);
  });

  it("follows a push-to-talk turn", () => {
    const { states } = run([
      { type: "PTT_START" }, { type: "LISTEN_END" }, { type: "SEND" }, { type: "ACCEPTED" }, { type: "FIRST_DELTA" }, { type: "STREAM_DONE" },
    ]);
    expect(states).toEqual(["listening", "transcribing", "understanding", "thinking", "responding", "standby"]);
  });

  it("returns to standby when nothing is said after the wake phrase", () => {
    expect(run([{ type: "WAKE" }, { type: "TRANSCRIPT_EMPTY" }]).states.at(-1)).toBe("standby");
    expect(run([{ type: "WAKE" }, { type: "AUTO_RETURN" }]).states.at(-1)).toBe("standby");
  });

  it("keeps responding when speech ends before the text stream", () => {
    const { states } = run([{ type: "SEND" }, { type: "ACCEPTED" }, { type: "SPEAK_START" }, { type: "SPEAK_END" }, { type: "STREAM_DONE" }]);
    expect(states).toEqual(["understanding", "thinking", "speaking", "responding", "standby"]);
  });

  it("supports barge-in, interruption and cancellation", () => {
    expect(run([{ type: "SEND" }, { type: "ACCEPTED" }, { type: "SPEAK_START" }, { type: "WAKE" }]).states.at(-1)).toBe("wake_detected");
    expect(run([{ type: "SEND" }, { type: "SPEAK_START" }, { type: "PTT_START" }]).states.at(-1)).toBe("listening");
    expect(run([{ type: "SPEAK_START" }, { type: "SPEAK_INTERRUPT" }]).states.at(-1)).toBe("interrupted");
    const cancelled = run([{ type: "SEND" }, { type: "FIRST_DELTA" }, { type: "ABORTED" }]);
    expect(cancelled.states.at(-1)).toBe("cancelled");
    expect(cancelled.s.context.streaming).toBe(false);
    expect(displayState(transition(cancelled.s, { type: "SEND" }))).toBe("understanding");
    expect(displayState(transition(cancelled.s, { type: "AUTO_RETURN" }))).toBe("standby");
  });

  it("ignores events that are not valid in the current state", () => {
    const s = transition(initialSnapshot, { type: "FIRST_DELTA" });
    expect(s.foreground).toBeNull();
    const listening = transition(initialSnapshot, { type: "PTT_START" });
    expect(transition(listening, { type: "SPEAK_END" }).foreground).toBe("listening");
    expect(transition(listening, { type: "STREAM_DONE" }).foreground).toBe("listening");
    expect(transition(initialSnapshot, { type: "ACCEPTED" }).foreground).toBeNull();
  });

  it("reports errors and keeps them until the user acts", () => {
    let s = transition(transition(initialSnapshot, { type: "SEND" }), { type: "STREAM_ERROR", message: "Cannot connect to Ollama" });
    expect(displayState(s)).toBe("error");
    expect(s.error).toBe("Cannot connect to Ollama");
    s = transition(s, { type: "CONTEXT", patch: { pendingApprovals: 2 } });
    expect(displayState(s)).toBe("error"); // background facts never hide an error
    expect(displayState(transition(s, { type: "AUTO_RETURN" }))).toBe("error"); // errors never time out
    s = transition(s, { type: "DISMISS" });
    expect(displayState(s)).toBe("awaiting_approval");
    expect(s.error).toBeNull();
  });

  it("reports microphone errors as voice errors", () => {
    const s = transition(initialSnapshot, { type: "VOICE_ERROR", message: "Microphone access is blocked." });
    expect(displayState(s)).toBe("error");
    expect(s.error).toBe("Microphone access is blocked.");
  });

  it("derives ambient states from observed context by priority", () => {
    const base = initialSnapshot.context;
    expect(ambientState(base)).toBe("standby");
    expect(ambientState({ ...base, wakeArmed: true })).toBe("standby");
    expect(ambientState({ ...base, pendingQuestion: true })).toBe("awaiting_user");
    expect(ambientState({ ...base, pendingQuestion: true, pendingApprovals: 1 })).toBe("awaiting_approval");
    expect(ambientState({ ...base, pendingApprovals: 1, job: "research" })).toBe("researching");
    expect(ambientState({ ...base, job: "design" })).toBe("designing");
    expect(ambientState({ ...base, job: "design", executing: 1 })).toBe("executing");
  });

  it("returns to the ambient state after a foreground activity", () => {
    let s = transition(initialSnapshot, { type: "CONTEXT", patch: { job: "research" } });
    expect(displayState(s)).toBe("researching");
    s = transition(s, { type: "PTT_START" });
    expect(displayState(s)).toBe("listening");
    s = transition(s, { type: "TRANSCRIPT_EMPTY" });
    expect(displayState(s)).toBe("researching");
  });

  it("only lists known events and states in the transition table", () => {
    const known = new Set(["WAKE", "PTT_START", "LISTEN_START", "LISTEN_END", "TRANSCRIPT_EMPTY", "TRANSCRIPT_DONE", "SEND",
      "ACCEPTED", "FIRST_DELTA", "STREAM_DONE", "STREAM_ERROR", "ABORTED", "CANCELLED", "SPEAK_START", "SPEAK_END",
      "SPEAK_INTERRUPT", "VOICE_ERROR", "ERROR", "DISMISS", "AUTO_RETURN", "CONTEXT"]);
    for (const row of Object.values(TRANSITIONS)) {
      for (const [ev, target] of Object.entries(row)) {
        expect(known.has(ev)).toBe(true);
        expect(target === "idle" || target in STATE_LABELS).toBe(true);
      }
    }
  });
});

describe("assistant store", () => {
  afterEach(() => {
    vi.useRealTimers();
    useAssistant.setState({ snapshot: initialSnapshot, state: "standby" });
  });

  it("returns to standby on its own after an interruption, but not after an error", () => {
    vi.useFakeTimers();
    const { dispatch } = useAssistant.getState();
    dispatch({ type: "SPEAK_START" });
    dispatch({ type: "SPEAK_INTERRUPT" });
    expect(useAssistant.getState().state).toBe("interrupted");
    vi.advanceTimersByTime(AUTO_RETURN_MS.interrupted! + 10);
    expect(useAssistant.getState().state).toBe("standby");

    dispatch({ type: "ERROR", message: "Boom" });
    vi.advanceTimersByTime(60_000);
    expect(useAssistant.getState().state).toBe("error");
  });

  it("cancels the automatic return when a new interaction starts", () => {
    vi.useFakeTimers();
    const { dispatch } = useAssistant.getState();
    dispatch({ type: "WAKE" });
    dispatch({ type: "LISTEN_START" });
    vi.advanceTimersByTime(AUTO_RETURN_MS.wake_detected! + 10);
    expect(useAssistant.getState().state).toBe("listening");
  });
});
