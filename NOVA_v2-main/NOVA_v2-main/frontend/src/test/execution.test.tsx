import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { dispatch, setCsrfToken } from "../api/client";
import { type AssistantEvent, displayState, initialSnapshot, type MachineSnapshot, transition } from "../assistant/machine";
import { CommandCenter } from "../pages/command/CommandCenter";
import { stepStates } from "../pages/command/ExecutionPanel";
import { useAssistant } from "../store/assistant";
import { useChat } from "../store/chat";
import { useProvidersStore } from "../store/providers";
import { mockApi, now, renderAt, sseResponse } from "./utils";

function states(events: AssistantEvent[]) {
  let s: MachineSnapshot = initialSnapshot;
  return events.map((e) => displayState((s = transition(s, e))));
}

describe("execution state", () => {
  it("shows executing while a tool runs inside the reply, and thinking again once it has finished", () => {
    expect(states([{ type: "SEND" }, { type: "ACCEPTED" }, { type: "ACTION_START" }, { type: "ACTION_END" }, { type: "FIRST_DELTA" }, { type: "STREAM_DONE" }]))
      .toEqual(["understanding", "thinking", "executing", "thinking", "responding", "standby"]);
  });

  it("never leaves executing behind when the reply ends or is stopped mid-action", () => {
    expect(states([{ type: "SEND" }, { type: "ACTION_START" }, { type: "ABORTED" }])).toEqual(["understanding", "executing", "cancelled"]);
    expect(states([{ type: "SEND" }, { type: "ACTION_START" }, { type: "STREAM_DONE" }, { type: "SEND" }])).toEqual(["understanding", "executing", "standby", "understanding"]);
  });

  it("parses the backend's task event", () => {
    const seen: unknown[] = [];
    const task = { status: "TASK_IN_PROGRESS", steps: [{ step: "read today's records", tool: "activity_today", state: "PENDING" }] };
    dispatch(`event: task\ndata: ${JSON.stringify(task)}`, { onTask: (t) => seen.push(t) });
    expect(seen).toEqual([task]);
  });
});

const base = {
  "GET /api/providers": () => ({ providers: [], selected: null }),
  "GET /api/projects": () => [],
  "GET /api/work": () => [],
  "GET /api/actions": () => [],
  "GET /api/suggestions": () => [],
  "GET /api/workflows": () => [],
};

beforeEach(() => {
  setCsrfToken("csrf");
  useChat.setState({ current: null, conversations: [], streaming: false, streamError: null });
  useAssistant.setState({ snapshot: initialSnapshot, state: "standby" });
  useProvidersStore.setState({ data: null, loaded: false, error: null });
});

describe("execution steps", () => {
  it("shows a step waiting for approval as waiting, and a step whose call is running as running", () => {
    const task = { status: "TASK_IN_PROGRESS" as const, steps: [
      { step: "save", tool: "word_document", state: "ACTION_FAILED" as const },
      { step: "open", tool: "open_app", state: "PENDING" as const },
      { step: "later", tool: "open_app", state: "PENDING" as const },
    ] };
    const actions = [
      { id: "1", phase: "finished" as const, tool: "word_document", arguments: {}, risk: "reversible", status: "awaiting_approval" as const },
      { id: "2", phase: "started" as const, tool: "open_app", arguments: {}, risk: "observe" },
    ];
    expect(stepStates(task, actions)).toEqual(["WAITING", "RUNNING", "PENDING"]);
  });
});

describe("execution panel", () => {
  it("shows the task's real steps, each call's outcome and its verification", async () => {
    const conv = { id: 9, title: "x", project_id: null, provider: null, model: null, created_at: now, updated_at: now };
    const steps = (a: string, b: string) => [
      { step: "read today's records", tool: "activity_today", state: a },
      { step: "write and save the Word document", tool: "word_document", state: b },
    ];
    const read = { id: "plan-0", phase: "finished", tool: "activity_today", arguments: {}, risk: "observe", status: "succeeded", verified: true, message: "Read 12 records." };
    const write = { id: "plan-1", phase: "finished", tool: "word_document", arguments: { action: "create", filename: "Report" }, risk: "reversible", status: "failed", message: "Word is not installed." };
    const done = { id: 2, role: "assistant", content: "Task partially completed.", status: "complete", error: null, provider: "x", model: "y", created_at: now, actions: [read, write] };
    mockApi({ ...base, "POST /api/conversations": () => conv, "GET /api/conversations": () => [conv],
      "POST /api/conversations/9/messages": () => sseResponse([
        ["start", { assistant_message_id: 2, provider: "x", model: "y", user_message: null }],
        ["task", { status: "TASK_IN_PROGRESS", steps: steps("PENDING", "PENDING") }],
        ["action", { ...read, phase: "started", status: undefined }],
        ["action", read],
        ["action", write],
        ["task", { status: "TASK_PARTIALLY_COMPLETED", steps: steps("ACTION_COMPLETED", "ACTION_FAILED") }],
        ["delta", { text: "Task partially completed." }],
        ["done", { message: done }],
      ]) });
    renderAt(<CommandCenter />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "write today's report in Word{Enter}");
    const panel = await screen.findByRole("region", { name: "Current task · Partly completed" });
    const list = within(panel).getByRole("list", { name: "Steps" });
    expect(within(list).getByText("read today's records").parentElement).toHaveTextContent("Done");
    expect(within(list).getByText("write and save the Word document").parentElement).toHaveTextContent("Failed");
    // execution and verification are reported separately, per call
    expect(within(panel).getByText("Done · verified")).toBeInTheDocument();
    expect(within(panel).getByText("Word is not installed.")).toBeInTheDocument();
  });
});
