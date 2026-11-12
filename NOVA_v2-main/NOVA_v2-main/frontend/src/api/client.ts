// Typed API client. Every state-changing request carries the CSRF token and the X-NOVA-Client header;
// the backend enforces both (the frontend is never the only enforcement point).

import type {
  ActionControl,
  ActionRecord,
  ActionRequest,
  ToolInfo,
  ApproveResult,
  Artifact,
  Attachment,
  Capability,
  ConversationMode,
  PhraseCheck,
  Preferences,
  PreferencesUpdate,
  VoiceCapabilities,
  LocalToolResult,
  SpotifyStatus,
  ArtifactKind,
  AuditEvent,
  AuthStatus,
  ChangeProposal,
  Conversation,
  ConversationDetail,
  FileContent,
  FileEntry,
  MemoryRecord,
  Message,
  Project,
  ProviderSelection,
  ProvidersResponse,
  ResearchResponse,
  Suggestion,
  Task,
  WorkEvent,
  WorkItem,
  WorkStatus,
  WorkflowDetail,
  WorkflowSummary,
  WorkflowTemplate,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function headersFor(method: string, hasBody: boolean): HeadersInit {
  const h: Record<string, string> = {};
  if (hasBody) h["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) {
    h["X-NOVA-Client"] = "web";
    if (csrfToken) h["X-CSRF-Token"] = csrfToken;
  }
  return h;
}

async function toApiError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `Request failed (HTTP ${res.status}).`;
  let details: unknown;
  try {
    const body = await res.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 422 && Array.isArray(details) && details.length) {
    const first = details[0] as { loc?: unknown[]; msg?: string };
    const field = first.loc?.filter((x) => x !== "body").join(".");
    message = field ? `${field}: ${first.msg}` : (first.msg ?? message);
  }
  return new ApiError(res.status, code, message, details);
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let res: Response;
  try {
    res = await fetch(withQuery(path, query), {
      method,
      credentials: "same-origin",
      headers: headersFor(method, body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the NOVA backend. Is it running?");
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function upload<T>(path: string, file: Blob, filename: string): Promise<T> {
  const form = new FormData();
  form.append("file", file, filename);
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", credentials: "same-origin", headers: headersFor("POST", false), body: form });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the NOVA backend. Is it running?");
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

// ---- streaming (SSE over fetch so we can POST with headers and abort) -----------------------------
export interface StreamHandlers {
  onStart?: (data: { assistant_message_id: number; provider: string; model: string; user_message: Message | null }) => void;
  onDelta?: (text: string) => void;
  onError?: (data: { message: string; kind: string; retryable: boolean }) => void;
  onDone?: (message: Message) => void;
  onAction?: (action: ActionRecord) => void; // a tool NOVA started or finished for this reply
}

export async function streamPost(path: string, body: unknown, handlers: StreamHandlers, signal: AbortSignal): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: headersFor("POST", true),
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, "network_error", "Cannot reach the NOVA backend. Is it running?");
  }
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new ApiError(0, "stream_error", "The response has no body.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        dispatch(block, handlers);
      }
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw e;
  }
}

export function dispatch(block: string, h: StreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return;
  const parsed = JSON.parse(data);
  if (event === "start") h.onStart?.(parsed);
  else if (event === "delta") h.onDelta?.(parsed.text);
  else if (event === "error") h.onError?.(parsed);
  else if (event === "done") h.onDone?.(parsed.message);
  else if (event === "action") h.onAction?.(parsed);
}

// ---- endpoint helpers ------------------------------------------------------------------------
const P = (id: number) => `/api/projects/${id}`;

export const api = {
  auth: {
    status: () => request<AuthStatus>("GET", "/api/auth/status"),
    setup: (b: { username: string; display_name: string; password: string; setup_code: string }) =>
      request<AuthStatus>("POST", "/api/auth/setup", b),
    login: (username: string, password: string) => request<AuthStatus>("POST", "/api/auth/login", { username, password }),
    logout: () => request<void>("POST", "/api/auth/logout"),
    changePassword: (current_password: string, new_password: string) =>
      request<AuthStatus>("POST", "/api/auth/password", { current_password, new_password }),
  },
  providers: {
    list: () => request<ProvidersResponse>("GET", "/api/providers"),
    select: (s: ProviderSelection) => request<ProviderSelection>("PUT", "/api/providers/selection", s),
  },
  conversations: {
    list: (q?: string, project_id?: number) => request<Conversation[]>("GET", "/api/conversations", undefined, { q, project_id }),
    create: (title?: string, project_id?: number | null, mode = "general") =>
      request<Conversation>("POST", "/api/conversations", { title: title || "New conversation", project_id: project_id ?? null, mode }),
    attach: (id: number, file: Blob, filename: string) => upload<Attachment>(`/api/conversations/${id}/attachments`, file, filename),
    get: (id: number) => request<ConversationDetail>("GET", `/api/conversations/${id}`),
    update: (id: number, b: { title?: string; project_id?: number; detach_project?: boolean; mode?: string }) =>
      request<Conversation>("PATCH", `/api/conversations/${id}`, b),
    remove: (id: number) => request<void>("DELETE", `/api/conversations/${id}`),
  },
  projects: {
    list: (include_archived = false) => request<Project[]>("GET", "/api/projects", undefined, { include_archived }),
    create: (b: { name: string; description: string; instructions: string; workspace_path?: string | null }) =>
      request<Project>("POST", "/api/projects", b),
    get: (id: number) => request<Project>("GET", P(id)),
    update: (id: number, b: Partial<Pick<Project, "name" | "description" | "instructions" | "archived">>) =>
      request<Project>("PATCH", P(id), b),
    remove: (id: number, confirm_name: string) => request<void>("DELETE", P(id), { confirm_name }),
    activity: (id: number) => request<AuditEvent[]>("GET", `${P(id)}/activity`),
    summarize: (id: number, focus = "") => request<Artifact>("POST", `${P(id)}/assist/summary`, { focus }),
  },
  files: {
    list: (pid: number, path = "") => request<FileEntry[]>("GET", `${P(pid)}/files`, undefined, { path }),
    read: (pid: number, path: string) => request<FileContent>("GET", `${P(pid)}/files/content`, undefined, { path }),
    create: (pid: number, path: string, type: "file" | "dir", content = "") =>
      request<FileEntry>("POST", `${P(pid)}/files`, { path, type, content }),
  },
  changes: {
    list: (pid: number, status?: string) => request<ChangeProposal[]>("GET", `${P(pid)}/changes`, undefined, { status }),
    propose: (pid: number, b: { path: string; content: string; summary?: string; base_sha256?: string | null }) =>
      request<ChangeProposal>("POST", `${P(pid)}/changes`, b),
    proposeAI: (pid: number, path: string, instruction: string) =>
      request<ChangeProposal>("POST", `${P(pid)}/changes/ai`, { path, instruction }),
    apply: (pid: number, id: number, proposed_sha256: string) =>
      request<ChangeProposal>("POST", `${P(pid)}/changes/${id}/apply`, { proposed_sha256 }),
    reject: (pid: number, id: number) => request<ChangeProposal>("POST", `${P(pid)}/changes/${id}/reject`),
    revert: (pid: number, id: number) => request<ChangeProposal>("POST", `${P(pid)}/changes/${id}/revert`),
  },
  actions: {
    requestCommand: (pid: number, argv: string[], reason: string) =>
      request<ActionRequest>("POST", `${P(pid)}/actions/command`, { argv, reason }),
    requestDelete: (pid: number, path: string, reason: string) =>
      request<ActionRequest>("POST", `${P(pid)}/actions/delete`, { path, reason }),
    requestMove: (pid: number, src: string, dst: string, reason: string) =>
      request<ActionRequest>("POST", `${P(pid)}/actions/move`, { src, dst, reason }),
    list: (status?: string, project_id?: number) => request<ActionRequest[]>("GET", "/api/actions", undefined, { status, project_id }),
    approve: (id: number, params_hash: string) =>
      request<ApproveResult>("POST", `/api/actions/${id}/approve`, { params_hash, confirm: true }),
    reject: (id: number) => request<ActionRequest>("POST", `/api/actions/${id}/reject`),
    control: () => request<ActionControl>("GET", "/api/actions/control"),
    emergencyStop: () => request<ActionControl & { cancelled_actions: number }>("POST", "/api/actions/emergency-stop"),
    resume: () => request<ActionControl>("POST", "/api/actions/resume"),
  },
  tasks: {
    list: (project_id?: number) => request<Task[]>("GET", "/api/tasks", undefined, { project_id }),
    get: (id: number) => request<Task>("GET", `/api/tasks/${id}`),
    cancel: (id: number) => request<Task>("POST", `/api/tasks/${id}/cancel`),
  },
  artifacts: {
    list: (pid: number, kind?: string, q?: string) => request<Artifact[]>("GET", `${P(pid)}/artifacts`, undefined, { kind, q }),
    create: (pid: number, b: { kind: ArtifactKind; title: string; content: string }) =>
      request<Artifact>("POST", `${P(pid)}/artifacts`, b),
    generate: (pid: number, kind: ArtifactKind, input: string, title?: string) =>
      request<Artifact>("POST", `${P(pid)}/artifacts/generate`, { kind, input, title: title || null }),
    update: (pid: number, id: number, b: { title?: string; content?: string }) =>
      request<Artifact>("PATCH", `${P(pid)}/artifacts/${id}`, b),
    remove: (pid: number, id: number) => request<void>("DELETE", `${P(pid)}/artifacts/${id}`),
    exportToFile: (pid: number, id: number, path: string) =>
      request<ChangeProposal>("POST", `${P(pid)}/artifacts/${id}/export`, { path }),
  },
  memory: {
    list: (q?: string, scope = "all") => request<MemoryRecord[]>("GET", "/api/memory", undefined, { q, scope }),
    create: (b: { kind: MemoryRecord["kind"]; content: string; project_id?: number | null }) =>
      request<MemoryRecord>("POST", "/api/memory", b),
    update: (id: number, b: Partial<Pick<MemoryRecord, "content" | "kind" | "enabled">>) =>
      request<MemoryRecord>("PATCH", `/api/memory/${id}`, b),
    remove: (id: number) => request<void>("DELETE", `/api/memory/${id}`),
    clear: (scope: "general" | "all") => request<void>("DELETE", "/api/memory", undefined, { scope, confirm: true }),
  },
  audit: {
    list: (action?: string, limit = 200) => request<AuditEvent[]>("GET", "/api/audit", undefined, { action, limit }),
  },
  workflows: {
    templates: () => request<WorkflowTemplate[]>("GET", "/api/workflow-templates"),
    list: (project_id?: number) => request<WorkflowSummary[]>("GET", "/api/workflows", undefined, { project_id }),
    create: (b: { idea: string; template: string; title?: string | null; project_id?: number | null }) =>
      request<WorkflowDetail>("POST", "/api/workflows", b),
    suggest: (idea: string) => request<{ template: string; label: string; reason: string }>("GET", "/api/workflow-templates/suggest", undefined, { idea }),
    skipStage: (id: number, stage: string) => request<WorkflowDetail>("POST", `/api/workflows/${id}/stages/${stage}/skip`),
    continueAll: (id: number, mode: "auto" | "ai" | "template" = "auto") =>
      request<WorkflowDetail>("POST", `/api/workflows/${id}/continue`, { mode }, { all: true }),
    dismiss: (id: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/dismiss`),
    get: (id: number) => request<WorkflowDetail>("GET", `/api/workflows/${id}`),
    answer: (id: number, qid: number, answer: string) =>
      request<WorkflowDetail>("POST", `/api/workflows/${id}/questions/${qid}/answer`, { answer }),
    skip: (id: number, qid: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/questions/${qid}/skip`),
    nextQuestion: (id: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/questions/next`, { mode: "auto" }),
    completeClarify: (id: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/clarify/complete`),
    runStage: (id: number, stage: string, mode: "auto" | "ai" | "template" = "auto") =>
      request<WorkflowDetail>("POST", `/api/workflows/${id}/stages/${stage}/run`, { mode }),
    continue: (id: number, mode: "auto" | "ai" | "template" = "auto") =>
      request<WorkflowDetail>("POST", `/api/workflows/${id}/continue`, { mode }),
    cancel: (id: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/cancel`),
    decide: (id: number, b: { decision: "approve" | "revise" | "reject"; proposal_sha256?: string; feedback?: string; reopen_stage?: string }) =>
      request<WorkflowDetail>("POST", `/api/workflows/${id}/decision`, b),
    archive: (id: number) => request<WorkflowDetail>("POST", `/api/workflows/${id}/archive`),
  },
  work: {
    list: (q: { project_id?: number; workflow_id?: number; status?: string } = {}) => request<WorkItem[]>("GET", "/api/work", undefined, q),
    create: (pid: number, b: { title: string; description?: string; domain?: string; executor?: WorkItem["executor"]; inputs?: Record<string, unknown>; depends_on?: number[] }) =>
      request<WorkItem>("POST", `${P(pid)}/work`, b),
    update: (id: number, b: { title?: string; description?: string; domain?: string; status?: WorkStatus; depends_on?: number[] }) =>
      request<WorkItem>("PATCH", `/api/work/${id}`, b),
    remove: (id: number) => request<void>("DELETE", `/api/work/${id}`),
    start: (id: number) => request<WorkItem>("POST", `/api/work/${id}/start`),
    cancel: (id: number) => request<WorkItem>("POST", `/api/work/${id}/cancel`),
    events: (id: number) => request<WorkEvent[]>("GET", `/api/work/${id}/events`),
  },
  preferences: {
    get: () => request<Preferences>("GET", "/api/preferences"),
    update: (b: PreferencesUpdate) => request<Preferences>("PUT", "/api/preferences", b),
  },
  voice: {
    capabilities: () => request<VoiceCapabilities>("GET", "/api/voice/capabilities"),
    checkPhrase: (phrase: string) => request<PhraseCheck>("POST", "/api/voice/check-phrase", { phrase }),
  },
  tools: {
    list: () => request<ToolInfo[]>("GET", "/api/tools"),
    execute: (name: string, arguments_: Record<string, unknown> = {}) =>
      request<LocalToolResult>("POST", "/api/tools/execute", { name, arguments: arguments_ }),
  },
  spotify: {
    status: () => request<SpotifyStatus>("GET", "/api/spotify/status"),
    disconnect: () => request<{ connected: boolean }>("POST", "/api/spotify/disconnect"),
  },
  capabilities: () => request<Capability[]>("GET", "/api/capabilities"),
  modes: () => request<ConversationMode[]>("GET", "/api/modes"),
  suggestions: (project_id?: number) => request<Suggestion[]>("GET", "/api/suggestions", undefined, { project_id }),
  research: {
    status: () => request<{ provider: string; available: boolean }>("GET", "/api/research/status"),
    search: (query: string, synthesize: boolean, project_id?: number | null) =>
      request<ResearchResponse>("POST", "/api/research", { query, synthesize, project_id: project_id ?? null }),
  },
};
