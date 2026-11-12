import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { setCsrfToken } from "../api/client";
import { FilesTab } from "../pages/project/FilesTab";
import { mockApi, now, renderAt } from "./utils";

const project = { id: 1, name: "Demo", description: "", instructions: "", workspace_path: "/w", archived: false, created_at: now, updated_at: now };

beforeEach(() => setCsrfToken("csrf"));

describe("file explorer", () => {
  it("asks for approval before moving or renaming a file", async () => {
    const { calls } = mockApi({
      "GET /api/projects/1/files": () => [{ name: "notes.md", path: "notes.md", type: "file", size: 5, modified: now }],
      "GET /api/projects/1/files/content": () => ({ path: "notes.md", content: "hello", sha256: "x", size: 5 }),
      "GET /api/projects/1/artifacts": () => [],
      "POST /api/projects/1/actions/move": (c) => ({ id: 4, project_id: 1, kind: "move_path", category: "B", params: { src: "notes.md", dst: (c.body as { dst: string }).dst },
        params_hash: "h", reason: "Move from file explorer", reversible: true, status: "pending", created_at: now, decided_at: null }),
    });
    renderAt(<FilesTab project={project} />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: /notes\.md/ }));
    await u.click(await screen.findByRole("button", { name: "Move or rename notes.md" }));
    const input = screen.getByLabelText(/New path inside the workspace/);
    await u.clear(input);
    await u.type(input, "docs/notes.md");
    await u.click(screen.getByRole("button", { name: "Review the move" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/projects/1/actions/move")?.body).toMatchObject({ src: "notes.md", dst: "docs/notes.md" }));
    expect(await screen.findByText("Move notes.md to docs/notes.md")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve and run" })).toBeDisabled(); // explicit confirmation first
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
  });
});
