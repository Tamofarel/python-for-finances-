import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { setCsrfToken } from "../api/client";
import { initialSnapshot } from "../assistant/machine";
import { CommandCenter } from "../pages/command/CommandCenter";
import { WorkflowPage } from "../pages/workflow/WorkflowPage";
import { WorkTab } from "../pages/project/WorkTab";
import { useAssistant } from "../store/assistant";
import { useChat } from "../store/chat";
import { useProvidersStore } from "../store/providers";
import { useVoice } from "../store/voice";
import { mockApi, now, renderAt } from "./utils";

const base = {
  "GET /api/providers": () => ({ providers: [], selected: null }),
  "GET /api/projects": () => [],
  "GET /api/work": () => [],
  "GET /api/actions": () => [],
  "GET /api/suggestions": () => [
    { kind: "question", priority: 80, title: "Answer: Which plants first?", detail: "", target: { type: "workflow", id: 5, project_id: 2 } },
  ],
  "GET /api/workflows": () => [],
};

beforeEach(() => {
  setCsrfToken("csrf");
  useChat.setState({ current: null, conversations: [], streaming: false, streamError: null });
  useAssistant.setState({ snapshot: initialSnapshot, state: "standby" });
  useProvidersStore.setState({ data: null, loaded: false, error: null });
});

describe("command center", () => {
  it("renders the SVG fallback core when WebGL is unavailable, with the real state", async () => {
    mockApi(base);
    renderAt(<CommandCenter />);
    expect(screen.getByRole("img", { name: "NOVA: Standing by" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Answer: Which plants first?" })).toBeInTheDocument();
    useAssistant.getState().dispatch({ type: "PTT_START" });
    expect(await screen.findByRole("img", { name: "NOVA: Listening" })).toBeInTheDocument();
  });

  it("opens an idea's waiting question in place from a suggestion", async () => {
    const q = { id: 11, topic: "subject_scope", question: "Which plants first?", rationale: "", options: ["Tomatoes"], source: "template", status: "open", answer: null, answered_at: null };
    const { calls } = mockApi({ ...base, "GET /api/workflows/5": () => wf({ questions: [q] }) });
    renderAt(<CommandCenter />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Answer: Which plants first?" }));
    expect(await screen.findByText("Which plants first?")).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/workflows/5")).toBe(true);
    sessionStorage.clear();
  });

  it("keeps text input available and sends through the conversation pipeline", async () => {
    const conv = { id: 9, title: "x", project_id: null, provider: null, model: null, created_at: now, updated_at: now };
    const { calls } = mockApi({ ...base, "POST /api/conversations": () => conv, "GET /api/conversations": () => [conv],
      "POST /api/conversations/9/messages": () => new Response("", { headers: { "Content-Type": "text/event-stream" } }) });
    renderAt(<CommandCenter />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "hello there{Enter}");
    await waitFor(() => expect(calls.find((c) => c.url === "/api/conversations/9/messages")?.body).toMatchObject({ content: "hello there" }));
  });

  it("turns a described idea into a focused workflow that starts by confirming the intent", async () => {
    const intent = { id: 21, topic: "intent", question: "Here is how I understood your idea: You want to build an app that helps people determine whether their plants are healthy using their smartphone camera. Did I get that right? Correct anything I misunderstood.",
      rationale: "", options: ["Yes, that's right"], source: "template", status: "open", answer: null, answered_at: null };
    const created = wf({ id: 7, template: "ml_vision_app", questions: [intent] });
    const { calls } = mockApi({ ...base, "POST /api/workflows": () => created, "GET /api/workflows/7": () => created,
      "POST /api/workflows/7/questions/21/answer": () => wf({ id: 7, questions: [{ ...intent, status: "answered", answer: "Yes, that's right" },
        { id: 22, topic: "users", question: "Who will use it?", rationale: "", options: [], source: "template", status: "open", answer: null, answered_at: null }] }),
      "GET /api/workflows/7/": () => created });
    renderAt(<CommandCenter />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "a plant-health photo app" }));
    await u.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/workflows" && c.method === "POST")?.body).toMatchObject({
      template: "auto", idea: "I want to build an app that helps people determine whether their plants are healthy using their smartphone camera." }));
    expect(await screen.findByText(/Here is how I understood your idea/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Not an idea\? Send it as a normal message/ })).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/messages"))).toBe(false); // an idea is not sent to the chat
    await u.click(screen.getByRole("button", { name: "Yes, that's right" }));
    expect(await screen.findByText("Who will use it?")).toBeInTheDocument();
    expect(screen.getByLabelText("Your answer")).toBeInTheDocument();
  });

  it("sends an idea-shaped message as a normal message when the owner says it is not an idea", async () => {
    const intent = { id: 31, topic: "intent", question: "Here is how I understood your idea: ... Did I get that right?", rationale: "",
      options: ["Yes, that's right"], source: "template", status: "open", answer: null, answered_at: null };
    const created = wf({ id: 8, idea: "I want to build a quick script to rename photos", questions: [intent] });
    const conv = { id: 9, title: "x", project_id: null, provider: null, model: null, created_at: now, updated_at: now };
    const { calls } = mockApi({ ...base, "POST /api/workflows": () => created, "GET /api/workflows/8": () => created,
      "POST /api/workflows/8/dismiss": () => ({ ...created, status: "archived" }),
      "POST /api/conversations": () => conv, "GET /api/conversations": () => [conv],
      "POST /api/conversations/9/messages": () => new Response("", { headers: { "Content-Type": "text/event-stream" } }) });
    renderAt(<CommandCenter />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "I want to build a quick script to rename photos{Enter}");
    await u.click(await screen.findByRole("button", { name: /Not an idea/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/workflows/8/dismiss")).toBe(true));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/conversations/9/messages")?.body).toMatchObject({ content: "I want to build a quick script to rename photos" }));
  });

  it("understands spoken commands while answering an idea's questions", async () => {
    const q = { id: 41, topic: "users", question: "Who will use it?", rationale: "", options: [], source: "template", status: "open", answer: null, answered_at: null };
    const current = wf({ id: 9, questions: [q] });
    sessionStorage.setItem("nova.focusWorkflow", "9");
    const { calls } = mockApi({ ...base, "GET /api/workflows/9": () => current,
      "POST /api/workflows/9/questions/41/skip": () => wf({ id: 9, questions: [{ ...q, status: "skipped" }], clarify_ready: true, next_stage: "research" }) });
    renderAt(<CommandCenter />);
    expect(await screen.findByText("Who will use it?")).toBeInTheDocument();
    // A spoken "skip" arrives through the voice pipeline and reaches the command center's transcript handler.
    const input = { name: "fake", supported: true, privacyNote: "", h: null as null | { onFinal: (t: string) => void; onEnd: () => void; onInterim: (t: string) => void },
      start(_l: string, h: never) { this.h = h; }, stop() {}, abort() {} };
    useVoice.setState({ input: input as never });
    await useVoice.getState().startListening("chat");
    input.h!.onFinal("Skip.");
    input.h!.onEnd();
    await waitFor(() => expect(calls.some((c) => c.url === "/api/workflows/9/questions/41/skip")).toBe(true));
    expect(await screen.findByText("I have enough to explore it.")).toBeInTheDocument();
    sessionStorage.clear();
  });
});

const wf = (over: Record<string, unknown> = {}) => ({
  id: 5, project_id: 2, title: "Plant app", idea: "Plant photo health", template: "ml_vision_app", template_label: "Image-recognition app",
  status: "active", current_stage: "clarify", running: false, clarify_ready: false, next_stage: null,
  stages: [{ id: "capture", label: "Capture the idea", status: "done", error: null, mode: null, note: null, started_at: null, finished_at: null, artifacts: [] },
           { id: "clarify", label: "Clarify problem and users", status: "running", error: null, mode: null, note: null, started_at: null, finished_at: null, artifacts: [] }],
  questions: [{ id: 11, topic: "subject_scope", question: "Which plants first?", rationale: "Scope drives accuracy.", options: ["Tomatoes"],
                source: "template", status: "open", answer: null, answered_at: null }],
  missing_decisions: [], research: [], proposal: null, revision_notes: [], work_items: [], events: [], created_at: now, updated_at: now, ...over,
});

describe("workflow page", () => {
  it("asks one question and sends the answer", async () => {
    const { calls } = mockApi({
      "GET /api/workflows/5": () => wf(),
      "POST /api/workflows/5/questions/11/answer": () => wf({ questions: [{ ...wf().questions[0], status: "answered", answer: "Tomatoes" }] }),
    });
    renderAt(<WorkflowPage />, "/workflows/5", "/workflows/:id");
    expect(await screen.findByRole("heading", { name: "Which plants first?" })).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Tomatoes" }));
    await u.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/answer"))?.body).toEqual({ answer: "Tomatoes" }));
  });

  it("approves the exact proposal version and shows pending research honestly", async () => {
    const proposal = { artifact_id: 30, sha256: "f".repeat(64), content: "# Proposal\nDecisions" };
    const { calls } = mockApi({
      "GET /api/workflows/5": () => wf({ status: "awaiting_decision", questions: [], proposal,
        stages: [...wf().stages.map((s) => ({ ...s, status: "done" }))],
        research: [{ id: 1, question: "Which datasets exist?", source_categories: ["dataset cards"], status: "pending", provider: null, results: [], error: null }] }),
      "POST /api/workflows/5/decision": () => wf({ status: "approved", questions: [], work_items: [{ id: 1, title: "Collect images", status: "todo", domain: "data" }] }),
    });
    renderAt(<WorkflowPage />, "/workflows/5", "/workflows/:id");
    expect(await screen.findByText(/0 of 1 questions have real sources/)).toBeInTheDocument();
    expect(screen.getByText(/Where to look: dataset cards/)).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Approve and create tasks" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/decision"))?.body).toEqual({ decision: "approve", proposal_sha256: "f".repeat(64) }));
    expect(await screen.findByText("Collect images")).toBeInTheDocument();
  });
});

describe("work board", () => {
  it("blocks starting a task until its dependencies are done", async () => {
    const item = (id: number, status: string, deps: number[] = []) => ({
      id, project_id: 2, workflow_id: null, title: `Task ${id}`, description: "", domain: "data", executor: "manual", status, inputs: {},
      outputs: {}, depends_on: deps, requires_approval: false, action_id: null, source: "user", error: null, position: id, created_at: now, updated_at: now,
    });
    const { calls } = mockApi({
      "GET /api/work": () => [item(1, "todo"), item(2, "todo", [1])],
      "GET /api/workflows": () => [],
      "POST /api/work/1/start": () => item(1, "in_progress"),
    });
    renderAt(<WorkTab project={{ id: 2, name: "P", description: "", instructions: "", workspace_path: "", archived: false, created_at: now, updated_at: now }} />);
    const starts = await screen.findAllByRole("button", { name: /Start/ });
    expect(starts[1]).toBeDisabled();
    expect(screen.getByText(/waiting for 1/)).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(starts[0]!);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/work/1/start")).toBe(true));
  });
});
