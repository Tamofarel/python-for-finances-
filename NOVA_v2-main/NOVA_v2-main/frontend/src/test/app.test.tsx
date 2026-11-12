import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../App";
import { useChat } from "../store/chat";
import { useProvidersStore } from "../store/providers";
import { useSession } from "../store/session";
import { errorResponse, mockApi, renderAt } from "./utils";

const user = { id: 1, username: "farel", display_name: "Farel", is_owner: true };
const providers = {
  providers: [
    { name: "ollama", label: "Ollama (local)", kind: "local", configured: true, reachable: false, models: [], default_model: "llama3.2", error: "Ollama is not reachable", supports_streaming: true },
    { name: "anthropic", label: "Anthropic Claude (cloud)", kind: "cloud", configured: false, reachable: false, models: [], default_model: "claude-sonnet-5-5", error: "NOVA_ANTHROPIC_API_KEY is not set.", supports_streaming: true },
  ],
  selected: { provider: "ollama", model: "llama3.2" },
};

function authedRoutes(extra: Record<string, (c: never) => unknown> = {}) {
  return {
    "GET /api/auth/status": () => ({ initialized: true, authenticated: true, user, csrf_token: "csrf-123" }),
    "GET /api/providers": () => providers,
    "GET /api/conversations": () => [],
    "GET /api/projects": () => [],
    "GET /api/actions": () => [],
    "GET /api/tasks": () => [],
    "GET /api/workflows": () => [],
    "GET /api/work": () => [],
    "GET /api/suggestions": () => [],
    "GET /api/preferences": () => ({ theme: "system", motion: "system", suggestions: true, wake_consent_at: null,
      voice: { wake_enabled: false, wake_phrase: "hey nova", sensitivity: "medium", stt_engine: "browser", tts_voice: null,
               speak_replies: true, acknowledge: true, chime: true, follow_up: false, lang: "en-US" } }),
    "GET /api/voice/capabilities": () => ({ enabled: true, remote_audio_allowed: false, sensitivities: ["low", "medium", "high"],
      wake: { id: "pocketsphinx", label: "PocketSphinx", available: true, local: true, reason: null, note: null }, stt_engines: [] }),
    ...extra,
  };
}

beforeEach(() => {
  useSession.setState({ loaded: false, initialized: false, user: null, loadError: null });
  useChat.setState({ current: null, conversations: [], streaming: false, streamError: null, query: "" });
  useProvidersStore.setState({ data: null, loaded: false, error: null });
});

describe("first run and login", () => {
  it("shows setup when NOVA is not initialized and sends the setup code", async () => {
    const { calls } = mockApi({
      ...authedRoutes(),
      "GET /api/auth/status": () => ({ initialized: false, authenticated: false, user: null, csrf_token: null }),
      "POST /api/auth/setup": () => ({ initialized: true, authenticated: true, user, csrf_token: "c" }),
    });
    renderAt(<App />);
    expect(await screen.findByRole("heading", { name: "Set up NOVA" })).toBeInTheDocument();
    const u = userEvent.setup();
    await u.type(screen.getByLabelText(/Setup code/), "12345678");
    await u.type(screen.getByLabelText(/^Username/), "farel");
    await u.type(screen.getByLabelText(/^Password/), "correct-horse-battery");
    await u.type(screen.getByLabelText(/Confirm password/), "correct-horse-battery");
    await u.click(screen.getByRole("button", { name: "Create owner account" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/setup")).toBe(true));
    const setup = calls.find((c) => c.url === "/api/auth/setup")!;
    expect(setup.body).toMatchObject({ username: "farel", setup_code: "12345678" });
    expect(setup.headers["X-NOVA-Client"]).toBe("web");
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("rejects mismatched passwords before calling the API", async () => {
    const { calls } = mockApi({ "GET /api/auth/status": () => ({ initialized: false, authenticated: false, user: null, csrf_token: null }) });
    renderAt(<App />);
    const u = userEvent.setup();
    await u.type(await screen.findByLabelText(/Setup code/), "1234");
    await u.type(screen.getByLabelText(/^Username/), "farel");
    await u.type(screen.getByLabelText(/^Password/), "correct-horse-battery");
    await u.type(screen.getByLabelText(/Confirm password/), "different-password");
    await u.click(screen.getByRole("button", { name: "Create owner account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
    expect(calls.some((c) => c.url === "/api/auth/setup")).toBe(false);
  });

  it("shows login errors from the backend", async () => {
    mockApi({
      "GET /api/auth/status": () => ({ initialized: true, authenticated: false, user: null, csrf_token: null }),
      "POST /api/auth/login": () => errorResponse(401, "invalid_credentials", "Invalid username or password."),
    });
    renderAt(<App />);
    const u = userEvent.setup();
    await u.type(await screen.findByLabelText("Username"), "farel");
    await u.type(screen.getByLabelText("Password"), "wrong");
    await u.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.");
  });

  it("explains when the backend is unreachable", async () => {
    mockApi({});
    useSession.setState({ loaded: false });
    globalThis.fetch = (async () => { throw new TypeError("fail"); }) as typeof fetch;
    renderAt(<App />);
    expect(await screen.findByText(/can't reach its backend/)).toBeInTheDocument();
  });
});

describe("main navigation", () => {
  it("renders every section and navigates with the keyboard", async () => {
    mockApi(authedRoutes({ "GET /api/memory": () => [], "GET /api/projects/": () => [] }));
    renderAt(<App />);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const name of ["Command", "Chat", "Ideas", "History", "Projects", "Approvals", "Memory", "Models", "Settings"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
    expect(nav).toBeInTheDocument();
    const u = userEvent.setup();
    screen.getByRole("link", { name: "Memory" }).focus();
    await u.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Memory" })).toBeInTheDocument();
  });

  it("shows the active model status with its real error", async () => {
    mockApi(authedRoutes());
    renderAt(<App />);
    expect(await screen.findByText(/Ollama is not reachable/)).toBeInTheDocument();
  });

  it("applies the owner's theme from the server and switches it from the rail", async () => {
    const { calls } = mockApi(authedRoutes({
      "PUT /api/preferences": (c: { body: unknown }) => ({ theme: "dark", motion: "system", suggestions: true, wake_consent_at: null,
        voice: { wake_enabled: false, wake_phrase: "hey nova", sensitivity: "medium", stt_engine: "browser", tts_voice: null,
                 speak_replies: true, acknowledge: true, chime: true, follow_up: false, lang: "en-US" }, ...(c.body as object) }),
    }));
    renderAt(<App />);
    const toggle = await screen.findByRole("button", { name: /Theme: follows your system/ });
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark")); // jsdom reports no light preference
    const u = userEvent.setup();
    await u.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.url === "/api/preferences")?.body).toEqual({ theme: "light" }));
  });

  it("shows the microphone as off and asks for consent before listening for the wake phrase", async () => {
    mockApi(authedRoutes());
    renderAt(<App />);
    const mic = await screen.findByRole("button", { name: /Microphone off/ });
    const u = userEvent.setup();
    await u.click(mic);
    expect(await screen.findByRole("dialog", { name: /Listen for “hey nova”/ })).toBeInTheDocument();
    expect(screen.getByText(/never saved or sent anywhere else/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
