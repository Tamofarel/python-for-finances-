import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { dispatch, setCsrfToken } from "../api/client";
import { ChatPage } from "../pages/ChatPage";
import { useChat } from "../store/chat";
import { errorResponse, mockApi, now, renderAt, sseResponse } from "./utils";

const conv = { id: 7, title: "New conversation", project_id: null, provider: null, model: null, created_at: now, updated_at: now };
const userMsg = { id: 1, role: "user", content: "Hello", status: "complete", error: null, provider: null, model: null, created_at: now };
const done = { id: 2, role: "assistant", content: "Hi Farel!", status: "complete", error: null, provider: "ollama", model: "llama3.2", created_at: now };

beforeEach(() => {
  setCsrfToken("csrf-123");
  useChat.setState({ current: null, conversations: [], streaming: false, streamError: null, query: "", override: null, presence: "idle" });
});

describe("SSE parsing", () => {
  it("dispatches start, delta, error and done events", () => {
    const seen: string[] = [];
    const h = {
      onStart: () => seen.push("start"),
      onDelta: (t: string) => seen.push(`delta:${t}`),
      onError: (e: { message: string }) => seen.push(`error:${e.message}`),
      onDone: () => seen.push("done"),
    };
    dispatch('event: start\ndata: {"assistant_message_id":1}', h);
    dispatch('event: delta\ndata: {"text":"Hi"}', h);
    dispatch('event: error\ndata: {"message":"boom","kind":"x","retryable":false}', h);
    dispatch('event: done\ndata: {"message":{}}', h);
    expect(seen).toEqual(["start", "delta:Hi", "error:boom", "done"]);
  });
});

describe("actions in the conversation", () => {
  const needsAuth = {
    id: "0-c1", phase: "finished", tool: "spotify_control", risk: "reversible",
    arguments: { action: "play_track", query: "Vie de Pirate" }, status: "needs_authorization",
    message: "Spotify isn't connected to NOVA yet. Connect your account, then ask again.", verified: false,
    data: { connect_url: "/api/spotify/connect", verified: false }, duration_ms: 12,
  };

  it("dispatches action events", () => {
    const seen: unknown[] = [];
    dispatch(`event: action\ndata: ${JSON.stringify(needsAuth)}`, { onAction: (a) => seen.push(a) });
    expect(seen).toEqual([needsAuth]);
  });

  it("shows what NOVA really did, with the blocker and how to resolve it", async () => {
    mockApi({
      "GET /api/projects": () => [],
      "GET /api/conversations": () => [conv],
      "POST /api/conversations": () => conv,
      "GET /api/conversations/7": () => ({ ...conv, messages: [] }),
      "POST /api/conversations/7/messages": () =>
        sseResponse([
          ["start", { assistant_message_id: 2, provider: "ollama", model: "llama3.2", user_message: userMsg }],
          ["action", { ...needsAuth, phase: "started", status: undefined, message: undefined }],
          ["action", needsAuth],
          ["delta", { text: "Connect Spotify first, then ask me again." }],
          ["done", { message: { ...done, content: "Connect Spotify first, then ask me again.", actions: [needsAuth] } }],
        ]),
    });
    renderAt(<ChatPage />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "play Vie de Pirate");
    await u.keyboard("{Enter}");
    expect(await screen.findByText("Needs a connection")).toBeInTheDocument();
    expect(screen.getByText(/Spotify isn't connected to NOVA yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect Spotify" })).toHaveAttribute("href", "/api/spotify/connect");
  });

  it("distinguishes verified success, unconfirmed outcomes and approvals", async () => {
    const ok = { ...needsAuth, id: "1", status: "succeeded", verified: true, message: "Playing Vie de Pirate by Tiakola.", data: { verified: true } };
    const maybe = { ...needsAuth, id: "2", status: "unverified", message: "Spotify accepted the request, but NOVA could not confirm it.", data: {} };
    const gated = { ...needsAuth, id: "3", tool: "home_control", status: "awaiting_approval", risk: "safety-critical",
                    arguments: { entity_id: "lock.front_door", action: "unlock" }, message: "Waits for your approval.", data: { action_id: 5 } };
    const browser = { ...needsAuth, id: "4", tool: "browser_open", status: "failed", risk: "reversible",
                      arguments: { url: "https://github.com/openai" }, message: "The page could not be verified." };
    mockApi({
      "GET /api/projects": () => [],
      "GET /api/conversations": () => [conv],
      "GET /api/conversations/7": () => ({ ...conv, messages: [userMsg, { ...done, actions: [ok, maybe, gated, browser] }] }),
    });
    renderAt(<ChatPage />, "/c/7", "/c/:id");
    expect(await screen.findByText("Done · verified")).toBeInTheDocument();
    expect(screen.getByText("Not confirmed")).toBeInTheDocument();
    expect(screen.getByText("Waiting for your approval")).toBeInTheDocument();
    expect(screen.getByText("Browser")).toBeInTheDocument();
    expect(screen.getByText("Open https://github.com/openai in NOVA's isolated browser")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review in Approvals" })).toHaveAttribute("href", "/approvals");
  });
});

describe("conversation", () => {
  it("creates a conversation, streams the reply and sends the CSRF token", async () => {
    const { calls } = mockApi({
      "GET /api/projects": () => [],
      "GET /api/conversations": () => [conv],
      "POST /api/conversations": () => conv,
      "GET /api/conversations/7": () => ({ ...conv, messages: [] }),
      "POST /api/conversations/7/messages": () =>
        sseResponse([
          ["start", { assistant_message_id: 2, provider: "ollama", model: "llama3.2", user_message: userMsg }],
          ["delta", { text: "Hi " }],
          ["delta", { text: "Farel!" }],
          ["done", { message: done }],
        ]),
    });
    renderAt(<ChatPage />);
    const u = userEvent.setup();
    const box = screen.getByLabelText("Message NOVA");
    await u.type(box, "Hello");
    await u.keyboard("{Enter}");
    expect(await screen.findByText("Hi Farel!")).toBeInTheDocument();
    const post = calls.find((c) => c.url === "/api/conversations/7/messages")!;
    expect(post.body).toMatchObject({ content: "Hello" });
    expect(post.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });

  it("Shift+Enter inserts a newline instead of sending", async () => {
    const { calls } = mockApi({ "GET /api/projects": () => [], "GET /api/conversations": () => [] });
    renderAt(<ChatPage />);
    const u = userEvent.setup();
    const box = screen.getByLabelText("Message NOVA");
    await u.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the provider's real error and keeps no fake answer", async () => {
    mockApi({
      "GET /api/projects": () => [],
      "GET /api/conversations": () => [conv],
      "POST /api/conversations": () => conv,
      "POST /api/conversations/7/messages": () =>
        sseResponse([
          ["start", { assistant_message_id: 2, provider: "ollama", model: "llama3.2", user_message: userMsg }],
          ["error", { message: "Cannot connect to Ollama at http://127.0.0.1:11434. Is it running?", kind: "unreachable", retryable: true }],
          ["done", { message: { ...done, content: "", status: "error", error: "Cannot connect to Ollama at http://127.0.0.1:11434. Is it running?" } }],
        ]),
    });
    renderAt(<ChatPage />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "Hello{Enter}");
    expect(await screen.findByText(/Cannot connect to Ollama/)).toBeInTheDocument();
    expect(screen.queryByText("Hi Farel!")).not.toBeInTheDocument();
  });

  it("reports a rejected request (e.g. provider not configured)", async () => {
    mockApi({
      "GET /api/projects": () => [],
      "GET /api/conversations": () => [conv],
      "POST /api/conversations": () => conv,
      "POST /api/conversations/7/messages": () => errorResponse(400, "provider_not_configured", "Provider 'Anthropic Claude (cloud)' is not configured. Open Settings → Providers."),
    });
    renderAt(<ChatPage />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Message NOVA"), "Hello{Enter}");
    expect(await screen.findByText(/is not configured/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open model settings" })).toBeInTheDocument();
    await waitFor(() => expect(useChat.getState().current?.messages).toEqual([]));
  });

  it("shows the empty state with starter prompts", async () => {
    mockApi({ "GET /api/projects": () => [], "GET /api/conversations": () => [] });
    renderAt(<ChatPage />);
    expect(screen.getByRole("heading", { name: "What are we working on?" })).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getAllByRole("button", { name: /Help me evaluate/ })[0]!);
    expect(screen.getByLabelText("Message NOVA")).toHaveValue("Help me evaluate an app idea: list the assumptions I should test first.");
  });
});
