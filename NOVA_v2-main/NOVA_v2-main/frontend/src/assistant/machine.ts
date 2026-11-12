// NOVA's assistant state machine (v2).
//
// Foreground states describe what NOVA is doing with you right now. They change only on explicit events emitted by
// real code paths: the wake-phrase detector, the speech recogniser, the conversation stream, speech output and your
// own actions. Ambient states describe background work observed from the backend (a workflow stage running, an
// approved command executing, approvals or questions waiting) and are derived from `context`.
// The displayed state is the foreground state when there is one, otherwise the highest-priority ambient state.
//
//   STANDBY → WAKE_DETECTED → LISTENING → TRANSCRIBING → UNDERSTANDING → THINKING → RESPONDING → SPEAKING → STANDBY
//   (+ RESEARCHING, DESIGNING, AWAITING_USER, AWAITING_APPROVAL, EXECUTING from observed backend state;
//    INTERRUPTED, CANCELLED, ERROR for interruptions, cancellations and failures)

export type ForegroundState =
  | "wake_detected"
  | "listening"
  | "transcribing"
  | "understanding"
  | "thinking"
  | "responding"
  | "speaking"
  | "interrupted"
  | "cancelled"
  | "error";

export type AmbientState = "executing" | "researching" | "designing" | "awaiting_approval" | "awaiting_user" | "standby";

export type AssistantState = ForegroundState | AmbientState;

export interface AssistantContext {
  streaming: boolean; // a request to NOVA is in flight (chat stream or workflow answer)
  job: "research" | "design" | null; // a workflow stage is running on the backend
  executing: number; // approved commands running (observed task status)
  pendingApprovals: number;
  pendingQuestion: boolean; // a workflow question waits for an answer
  wakeArmed: boolean; // the local wake-phrase detector is listening for the wake phrase
}

export type AssistantEvent =
  | { type: "WAKE" } // the local detector heard the wake phrase
  | { type: "PTT_START" } // the owner pressed Talk (recognition is starting)
  | { type: "LISTEN_START" } // a recogniser actually started capturing speech
  | { type: "LISTEN_END" } // the owner released Talk / speech ended; waiting for the transcript
  | { type: "TRANSCRIPT_EMPTY" }
  | { type: "TRANSCRIPT_DONE" } // transcript placed in a field without sending
  | { type: "SEND" } // a request was sent to NOVA
  | { type: "ACCEPTED" } // the server accepted it and called the model (SSE "start")
  | { type: "FIRST_DELTA" }
  | { type: "STREAM_DONE" } // the request finished
  | { type: "STREAM_ERROR"; message: string }
  | { type: "ABORTED" } // the owner stopped the reply
  | { type: "CANCELLED" } // the owner cancelled background work
  | { type: "SPEAK_START" }
  | { type: "SPEAK_END" }
  | { type: "SPEAK_INTERRUPT" } // speech stopped by the owner or by barge-in
  | { type: "VOICE_ERROR"; message: string }
  | { type: "ERROR"; message: string }
  | { type: "DISMISS" }
  | { type: "AUTO_RETURN" } // a short display timeout after an interaction (never used to fake work)
  | { type: "CONTEXT"; patch: Partial<AssistantContext> };

export type EventType = AssistantEvent["type"];

export interface MachineSnapshot {
  foreground: ForegroundState | null;
  context: AssistantContext;
  error: string | null;
}

export const initialSnapshot: MachineSnapshot = {
  foreground: null,
  context: { streaming: false, job: null, executing: 0, pendingApprovals: 0, pendingQuestion: false, wakeArmed: false },
  error: null,
};

type Target = ForegroundState | "idle";

// Explicit transition table. "idle" = no foreground activity (display falls back to the ambient state).
// The "ambient" row applies when there is no foreground state.
export const TRANSITIONS: Record<ForegroundState | "ambient", Partial<Record<EventType, Target>>> = {
  ambient: { WAKE: "wake_detected", PTT_START: "listening", LISTEN_START: "listening", SEND: "understanding",
             SPEAK_START: "speaking", ERROR: "error", VOICE_ERROR: "error", CANCELLED: "cancelled" },
  wake_detected: { LISTEN_START: "listening", PTT_START: "listening", TRANSCRIPT_EMPTY: "idle", AUTO_RETURN: "idle",
                   SEND: "understanding", VOICE_ERROR: "error", SPEAK_INTERRUPT: "interrupted" },
  listening: { LISTEN_END: "transcribing", TRANSCRIPT_EMPTY: "idle", TRANSCRIPT_DONE: "idle", SEND: "understanding",
               VOICE_ERROR: "error", ABORTED: "cancelled" },
  transcribing: { SEND: "understanding", TRANSCRIPT_EMPTY: "idle", TRANSCRIPT_DONE: "idle", VOICE_ERROR: "error",
                  PTT_START: "listening", WAKE: "wake_detected" },
  understanding: { ACCEPTED: "thinking", FIRST_DELTA: "responding", STREAM_DONE: "idle", STREAM_ERROR: "error",
                   ABORTED: "cancelled", SPEAK_START: "speaking", PTT_START: "listening", WAKE: "wake_detected" },
  thinking: { FIRST_DELTA: "responding", SPEAK_START: "speaking", STREAM_DONE: "idle", STREAM_ERROR: "error",
              ABORTED: "cancelled", PTT_START: "listening", WAKE: "wake_detected" },
  responding: { SPEAK_START: "speaking", STREAM_DONE: "idle", STREAM_ERROR: "error", ABORTED: "cancelled",
                PTT_START: "listening", WAKE: "wake_detected" },
  speaking: { SPEAK_END: "idle", SPEAK_INTERRUPT: "interrupted", PTT_START: "listening", WAKE: "wake_detected",
              STREAM_ERROR: "error", ABORTED: "cancelled" },
  interrupted: { PTT_START: "listening", WAKE: "wake_detected", SEND: "understanding", DISMISS: "idle",
                 AUTO_RETURN: "idle", SPEAK_START: "speaking", ERROR: "error", LISTEN_START: "listening" },
  cancelled: { PTT_START: "listening", WAKE: "wake_detected", SEND: "understanding", DISMISS: "idle",
               AUTO_RETURN: "idle", ERROR: "error", LISTEN_START: "listening" },
  error: { PTT_START: "listening", WAKE: "wake_detected", SEND: "understanding", DISMISS: "idle", LISTEN_START: "listening" },
};

/** Foreground states that return to standby on their own after a short pause (errors wait for the owner). */
export const AUTO_RETURN_MS: Partial<Record<ForegroundState, number>> = {
  interrupted: 3500,
  cancelled: 3500,
  wake_detected: 6000, // nothing was heard after the wake phrase
};

export function ambientState(ctx: AssistantContext): AmbientState {
  if (ctx.executing > 0) return "executing";
  if (ctx.job === "research") return "researching";
  if (ctx.job === "design") return "designing";
  if (ctx.pendingApprovals > 0) return "awaiting_approval";
  if (ctx.pendingQuestion) return "awaiting_user";
  return "standby";
}

export function displayState(s: MachineSnapshot): AssistantState {
  return s.foreground ?? ambientState(s.context);
}

/** Apply one event. Events that are not valid in the current state leave the foreground state unchanged. */
export function transition(s: MachineSnapshot, event: AssistantEvent): MachineSnapshot {
  let context = s.context;
  if (event.type === "CONTEXT") return { ...s, context: { ...context, ...event.patch } };
  if (event.type === "SEND") context = { ...context, streaming: true };
  if (event.type === "STREAM_DONE" || event.type === "STREAM_ERROR" || event.type === "ABORTED") {
    context = { ...context, streaming: false };
  }

  const row = TRANSITIONS[s.foreground ?? "ambient"];
  let target = row[event.type];
  // Speech can end before the text stream does: keep showing that NOVA is still responding.
  if (event.type === "SPEAK_END" && s.foreground === "speaking" && context.streaming) target = "responding";
  // A finished stream while NOVA is still speaking it aloud keeps the speaking state.
  if (event.type === "STREAM_DONE" && s.foreground === "speaking") target = undefined;
  if (target === undefined) return { ...s, context };
  const error = target === "error" && "message" in event ? event.message : target === "error" ? s.error : null;
  return { foreground: target === "idle" ? null : target, context, error };
}

export const STATE_LABELS: Record<AssistantState, string> = {
  standby: "Standing by",
  wake_detected: "Wake phrase heard",
  listening: "Listening",
  transcribing: "Transcribing",
  understanding: "Understanding",
  thinking: "Thinking",
  responding: "Responding",
  speaking: "Speaking",
  researching: "Researching",
  designing: "Designing",
  awaiting_user: "Waiting for your answer",
  awaiting_approval: "Awaiting your approval",
  executing: "Running an approved action",
  interrupted: "Interrupted",
  cancelled: "Cancelled",
  error: "Something went wrong",
};
