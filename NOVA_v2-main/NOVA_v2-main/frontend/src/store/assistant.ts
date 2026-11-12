import { create } from "zustand";
import {
  AUTO_RETURN_MS,
  type AssistantEvent,
  type AssistantState,
  displayState,
  initialSnapshot,
  type MachineSnapshot,
  transition,
} from "../assistant/machine";

interface AssistantStore {
  snapshot: MachineSnapshot;
  state: AssistantState;
  detail: string; // a short factual line about the current state (e.g. which stage is running)
  history: { at: number; event: AssistantEvent["type"]; state: AssistantState }[];
  dispatch: (event: AssistantEvent) => void;
  setDetail: (detail: string) => void;
}

let returnTimer: ReturnType<typeof setTimeout> | null = null;

export const useAssistant = create<AssistantStore>((set, get) => ({
  snapshot: initialSnapshot,
  state: displayState(initialSnapshot),
  detail: "",
  history: [],
  dispatch: (event) => {
    const prev = get().snapshot;
    const next = transition(prev, event);
    const state = displayState(next);
    const history = event.type === "CONTEXT" && state === get().state
      ? get().history
      : [...get().history.slice(-40), { at: Date.now(), event: event.type, state }];
    set({ snapshot: next, state, history });
    // Some states return to standby on their own after a short pause (see AUTO_RETURN_MS).
    if (next.foreground !== prev.foreground) {
      if (returnTimer) clearTimeout(returnTimer);
      returnTimer = null;
      const fg = next.foreground;
      const ms = fg ? AUTO_RETURN_MS[fg] : undefined;
      if (fg && ms) {
        returnTimer = setTimeout(() => {
          if (get().snapshot.foreground === fg) get().dispatch({ type: "AUTO_RETURN" });
        }, ms);
      }
    }
  },
  setDetail: (detail) => set({ detail }),
}));

export const dispatchAssistant = (event: AssistantEvent) => useAssistant.getState().dispatch(event);
