// Connects real application events to the assistant state machine. Mounted once by the layout.

import { useEffect } from "react";
import { api } from "../api/client";
import { dispatchAssistant, useAssistant } from "../store/assistant";
import { onChatStream } from "../store/chat";

export interface ObservedContext {
  pendingApprovals: number;
  executing: number;
  job: "research" | "design" | null;
  jobLabel: string;
  pendingQuestion: boolean;
}

/** Read background state from the backend (observed, never simulated). */
export async function observeBackend(): Promise<ObservedContext> {
  const [actions, tasks, workflows] = await Promise.all([
    api.actions.list("pending"),
    api.tasks.list(),
    api.workflows.list(),
  ]);
  const running = workflows.find((w) => w.running);
  return {
    pendingApprovals: actions.length,
    executing: tasks.filter((t) => t.status === "running" || t.status === "queued").length,
    job: running ? (running.current_stage === "research" ? "research" : "design") : null,
    jobLabel: running ? `${running.current_stage_label} — ${running.title}` : "",
    pendingQuestion: workflows.some((w) => w.status === "active" && w.open_question !== null),
  };
}

export function useAssistantWiring(pollMs = 4000): void {
  useEffect(() => {
    const off = onChatStream((e) => {
      if (e.type === "send") dispatchAssistant({ type: "SEND" });
      else if (e.type === "start") dispatchAssistant({ type: "ACCEPTED" });
      else if (e.type === "delta" && e.first) dispatchAssistant({ type: "FIRST_DELTA" });
      else if (e.type === "error") dispatchAssistant({ type: "STREAM_ERROR", message: e.message });
      else if (e.type === "aborted") dispatchAssistant({ type: "ABORTED" });
      else if (e.type === "end" && e.outcome === "done") dispatchAssistant({ type: "STREAM_DONE" });
    });
    let alive = true;
    const poll = async () => {
      try {
        const o = await observeBackend();
        if (!alive) return;
        dispatchAssistant({
          type: "CONTEXT",
          patch: { pendingApprovals: o.pendingApprovals, executing: o.executing, job: o.job, pendingQuestion: o.pendingQuestion },
        });
        useAssistant.getState().setDetail(o.jobLabel);
      } catch {
        /* backend unreachable: keep the last observed context */
      }
    };
    void poll();
    const id = setInterval(poll, pollMs);
    const onFocus = () => void poll();
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      off();
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [pollMs]);
}
