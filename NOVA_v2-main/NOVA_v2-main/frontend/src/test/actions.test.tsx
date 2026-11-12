import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { setCsrfToken } from "../api/client";
import { ActionsSettings } from "../pages/settings/ActionsSettings";
import { mockApi, renderAt } from "./utils";

const tools = [
  { name: "spotify_control", description: "Control Spotify.", permission: "routine", risk: "reversible", enabled: true,
    unavailable_reason: null, timeout_s: 45, parameters: {} },
  { name: "home_control", description: "Smart home.", permission: "routine", risk: "reversible", enabled: false,
    unavailable_reason: "Home Assistant is not configured.", timeout_s: 20, parameters: {} },
];

beforeEach(() => setCsrfToken("csrf"));

describe("Actions settings", () => {
  it("lists what NOVA can and cannot do, with the reason", async () => {
    mockApi({ "GET /api/tools": () => tools, "GET /api/actions/control": () => ({ paused: false, running: 0 }) });
    renderAt(<ActionsSettings />);
    expect(await screen.findByText("Spotify")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant is not configured.")).toBeInTheDocument();
  });

  it("emergency stop pauses device control until resumed", async () => {
    let paused = false;
    const { calls } = mockApi({
      "GET /api/tools": () => tools,
      "GET /api/actions/control": () => ({ paused, running: paused ? 0 : 1 }),
      "POST /api/actions/emergency-stop": () => {
        paused = true;
        return { paused: true, cancelled_actions: 1, cancelled_commands: 0, rejected_requests: 0 };
      },
      "POST /api/actions/resume": () => {
        paused = false;
        return { paused: false };
      },
    });
    renderAt(<ActionsSettings />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /Emergency stop/ }));
    expect(await screen.findByText(/Device control is paused/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/actions/emergency-stop")!.headers["X-CSRF-Token"]).toBe("csrf");
    await u.click(screen.getByRole("button", { name: "Resume device control" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/actions/resume")).toBe(true));
    expect(await screen.findByRole("button", { name: /Emergency stop/ })).toBeInTheDocument();
  });
});
