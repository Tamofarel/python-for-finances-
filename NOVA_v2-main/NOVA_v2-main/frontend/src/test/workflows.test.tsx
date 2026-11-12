import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { setCsrfToken } from "../api/client";
import type { ActionRequest } from "../api/types";
import { ApprovalDialog } from "../components/Approval";
import { DiffView } from "../components/DiffView";
import { ProjectsPage } from "../pages/ProjectsPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { FilesTab } from "../pages/project/FilesTab";
import { splitArgs } from "../pages/project/TasksTab";
import { useProvidersStore } from "../store/providers";
import { mockApi, now, renderAt } from "./utils";

const project = { id: 3, name: "CampusLink", description: "", instructions: "", workspace_path: "/data/ws", archived: false, created_at: now, updated_at: now };

beforeEach(() => {
  setCsrfToken("csrf-123");
  useProvidersStore.setState({ data: null, loaded: false, error: null });
});

describe("DiffView", () => {
  it("marks added and removed lines with line numbers", () => {
    renderAt(<DiffView diff={"--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"} />);
    const region = screen.getByRole("region", { name: "Diff" });
    expect(within(region).getByText("removed:")).toBeInTheDocument();
    expect(within(region).getByText("added:")).toBeInTheDocument();
    expect(region.querySelector(".diff-add")?.textContent).toContain("+new");
  });
});

describe("approval dialog", () => {
  const action: ActionRequest = {
    id: 9, project_id: 3, kind: "run_command", category: "C", params: { argv: ["python", "-m", "pytest", "-q"], cwd: "." },
    params_hash: "a".repeat(64), reason: "check the fix", reversible: false, status: "pending", created_at: now, decided_at: null,
  };

  it("requires explicit confirmation and sends the parameter hash", async () => {
    const task = { id: 1, project_id: 3, action_id: 9, kind: "command", status: "succeeded", command: ["python", "-m", "pytest", "-q"], exit_code: 0, stdout: "1 passed", stderr: "", error: null, duration_seconds: 0.5, created_at: now, started_at: now, finished_at: now };
    const { calls } = mockApi({ "POST /api/actions/9/approve": () => ({ action: { ...action, status: "approved" }, task, result: null }) });
    renderAt(<ApprovalDialog action={action} onClose={() => {}} onDecided={() => {}} />);
    expect(screen.getByText("python -m pytest -q", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve and run" });
    expect(approve).toBeDisabled();
    const u = userEvent.setup();
    await u.click(screen.getByRole("checkbox"));
    expect(approve).toBeEnabled();
    await u.click(approve);
    expect(await screen.findByText("1 passed")).toBeInTheDocument();
    expect(screen.getByText("Exit code 0")).toBeInTheDocument();
    expect(calls[0]!.body).toEqual({ params_hash: "a".repeat(64), confirm: true });
  });

  it("closes with Escape and can reject", async () => {
    let closed = false;
    const { calls } = mockApi({ "POST /api/actions/9/reject": () => ({ ...action, status: "rejected" }) });
    renderAt(<ApprovalDialog action={action} onClose={() => { closed = true; }} onDecided={() => {}} />);
    const u = userEvent.setup();
    await u.keyboard("{Escape}");
    expect(closed).toBe(true);
    closed = false;
    await u.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/actions/9/reject")).toBe(true));
  });
});

describe("provider settings", () => {
  it("shows honest status and saves the selection", async () => {
    const data = {
      providers: [
        { name: "ollama", label: "Ollama (local)", kind: "local", configured: true, reachable: true, models: ["llama3.2:latest", "qwen2.5:3b"], default_model: "llama3.2", error: null, supports_streaming: true },
        { name: "anthropic", label: "Anthropic Claude (cloud)", kind: "cloud", configured: false, reachable: false, models: [], default_model: "claude-sonnet-5-5", error: "NOVA_ANTHROPIC_API_KEY is not set.", supports_streaming: true },
      ],
      selected: { provider: "ollama", model: "llama3.2:latest" },
    };
    const { calls } = mockApi({ "GET /api/providers": () => data, "PUT /api/providers/selection": (c) => c.body });
    renderAt(<ProvidersPage />);
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    const u = userEvent.setup();
    await u.selectOptions(screen.getByRole("combobox"), "qwen2.5:3b");
    await u.click(screen.getByRole("button", { name: "Use this model" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ provider: "ollama", model: "qwen2.5:3b" }));
    expect(await screen.findByText(/will use qwen2.5:3b/)).toBeInTheDocument();
  });
});

describe("projects", () => {
  it("shows the empty state and creates a project", async () => {
    const { calls } = mockApi({ "GET /api/projects": () => [], "POST /api/projects": () => project });
    renderAt(<ProjectsPage />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Create your first project" }));
    await u.type(screen.getByLabelText("Name"), "CampusLink");
    await u.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "CampusLink", workspace_path: null }));
  });
});

describe("file editing", () => {
  it("turns an edit into a reviewed proposal and applies it with the content hash", async () => {
    const proposal = { id: 5, project_id: 3, path: "app.py", operation: "modify", base_sha256: "b".repeat(64), proposed_sha256: "c".repeat(64), diff: "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-print(1)\n+print(2)\n", summary: "Manual edit", source: "user", status: "pending", created_at: now, decided_at: null };
    let content = "print(1)\n";
    const { calls } = mockApi({
      "GET /api/projects/3/files": () => [{ name: "app.py", path: "app.py", type: "file", size: 9, modified: now }],
      "GET /api/projects/3/files/content": () => ({ path: "app.py", content, sha256: "b".repeat(64), size: 9 }),
      "POST /api/projects/3/changes": () => proposal,
      "POST /api/projects/3/changes/5/apply": () => { content = "print(2)\n"; return { ...proposal, status: "applied" }; },
    });
    renderAt(<FilesTab project={project} />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /app\.py/ }));
    const editor = await screen.findByLabelText("Contents of app.py");
    await u.clear(editor);
    await u.type(editor, "print(2)\n");
    await u.click(screen.getByRole("button", { name: "Review changes" }));
    expect(await screen.findByRole("region", { name: "Diff" })).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/projects/3/changes")?.body).toMatchObject({ path: "app.py", base_sha256: "b".repeat(64) });
    await u.click(screen.getByRole("button", { name: "Apply change" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/apply"))?.body).toEqual({ proposed_sha256: "c".repeat(64) }));
    expect(await screen.findByRole("button", { name: "Revert to previous version" })).toBeInTheDocument();
  });
});

describe("command parsing", () => {
  it("splits arguments without any shell interpretation", () => {
    expect(splitArgs('python -m pytest -k "slow and not db"')).toEqual(["python", "-m", "pytest", "-k", "slow and not db"]);
    expect(splitArgs("npm test; rm -rf /")).toEqual(["npm", "test;", "rm", "-rf", "/"]);
  });
});
