// Types mirroring the backend Pydantic schemas (backend/app/schemas/__init__.py).

export interface User {
  id: number;
  username: string;
  display_name: string;
  is_owner: boolean;
}

export interface AuthStatus {
  initialized: boolean;
  authenticated: boolean;
  user: User | null;
  csrf_token: string | null;
}

export interface ProviderStatus {
  name: string;
  label: string;
  kind: "local" | "cloud";
  configured: boolean;
  reachable: boolean;
  models: string[];
  default_model: string;
  error: string | null;
  supports_streaming: boolean;
}

export interface ProviderSelection {
  provider: string;
  model: string;
}

export interface ProvidersResponse {
  providers: ProviderStatus[];
  selected: ProviderSelection | null;
}

export type MessageStatus = "complete" | "streaming" | "error" | "cancelled";

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  error: string | null;
  provider: string | null;
  model: string | null;
  created_at: string;
  attachments?: Attachment[];
  actions?: ActionRecord[] | null;
}

/** succeeded: done (and verified where the integration can verify) · unverified: accepted but not confirmed. */
export type ActionStatus =
  | "succeeded" | "unverified" | "needs_input" | "needs_authorization" | "awaiting_approval"
  | "unavailable" | "denied" | "failed" | "timed_out" | "cancelled";

/** One tool call NOVA ran (or refused, or queued for approval) for a reply, with its real result. */
export interface ActionRecord {
  id: string;
  phase: "started" | "finished";
  tool: string;
  arguments: Record<string, unknown>;
  risk: string;
  status?: ActionStatus;
  message?: string;
  verified?: boolean;
  data?: Record<string, unknown>;
  duration_ms?: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  permission: "read" | "routine" | "approval";
  risk: "observe" | "reversible" | "safety-critical";
  enabled: boolean;
  unavailable_reason: string | null;
  timeout_s: number;
  parameters: Record<string, unknown>;
}

export interface ActionControl {
  paused: boolean;
  running: number;
}

export interface Conversation {
  id: number;
  title: string;
  mode?: string;
  project_id: number | null;
  provider: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface Project {
  id: number;
  name: string;
  description: string;
  instructions: string;
  workspace_path: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size: number;
  modified: string;
}

export interface FileContent {
  path: string;
  content: string;
  sha256: string;
  size: number;
}

export interface ChangeProposal {
  id: number;
  project_id: number;
  path: string;
  operation: "create" | "modify";
  base_sha256: string | null;
  proposed_sha256: string;
  diff: string;
  summary: string;
  source: "user" | "ai";
  status: "pending" | "applied" | "rejected" | "reverted" | "stale";
  created_at: string;
  decided_at: string | null;
}

export interface ActionRequest {
  id: number;
  project_id: number | null; // null for device actions (kind "tool_call")
  kind: "run_command" | "delete_path" | "move_path" | "tool_call";
  category: "A" | "B" | "C";
  params: Record<string, unknown>;
  params_hash: string;
  reason: string;
  reversible: boolean;
  status: "pending" | "approved" | "rejected" | "expired";
  created_at: string;
  decided_at: string | null;
}

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out";

export interface Task {
  id: number;
  project_id: number | null;
  action_id: number | null;
  kind: string;
  status: TaskStatus;
  command: string[] | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  error: string | null;
  duration_seconds: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ApproveResult {
  action: ActionRequest;
  task: Task | null;
  result: Record<string, unknown> | null;
}

export type ArtifactKind =
  | "idea_analysis"
  | "requirements"
  | "user_stories"
  | "architecture"
  | "api_contract"
  | "data_model"
  | "ui_spec"
  | "component_inventory"
  | "implementation_plan"
  | "research_brief"
  | "review_summary"
  | "project_summary"
  | "note"
  | "feasibility"
  | "technology_choices"
  | "risk_register"
  | "research_plan"
  | "proposal";

export interface Artifact {
  id: number;
  project_id: number;
  kind: ArtifactKind;
  title: string;
  content: string;
  created_by: "user" | "ai";
  source_input: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryRecord {
  id: number;
  kind: "preference" | "fact" | "note";
  content: string;
  project_id: number | null;
  source: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditEvent {
  id: number;
  action: string;
  target: string;
  outcome: "success" | "denied" | "failure";
  details: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
  source: string;
}

export interface ResearchResponse {
  query: string;
  provider: string;
  results: SearchResult[];
  brief: string | null;
  artifact_id: number | null;
}

// ---- 0.2: workflows and the task engine ---------------------------------------------------------------
export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed" | "cancelled" | "interrupted";

export interface WorkflowStage {
  id: string;
  label: string;
  status: StageStatus;
  error: string | null;
  mode: "ai" | "template" | null;
  note: string | null;
  started_at: string | null;
  finished_at: string | null;
  artifacts: { id: number; kind: ArtifactKind; title: string; created_by: "ai" | "user" | null; edited: boolean }[];
}

export interface WorkflowQuestion {
  id: number;
  topic: string;
  question: string;
  rationale: string;
  options: string[];
  source: "template" | "ai";
  status: "open" | "answered" | "skipped";
  answer: string | null;
  answered_at: string | null;
}

export interface ResearchItem {
  id: number;
  question: string;
  source_categories: string[];
  status: "pending" | "sourced" | "no_results" | "failed";
  provider: string | null;
  results: SearchResult[];
  error: string | null;
}

export interface WorkflowEvent {
  id: number;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface WorkflowDetail {
  id: number;
  project_id: number;
  title: string;
  idea: string;
  template: string;
  template_label: string;
  status: "active" | "awaiting_decision" | "approved" | "rejected" | "archived";
  current_stage: string;
  running: boolean;
  clarify_ready: boolean;
  next_stage: string | null;
  stages: WorkflowStage[];
  questions: WorkflowQuestion[];
  missing_decisions: { topic: string; question: string }[];
  research: ResearchItem[];
  proposal: { artifact_id: number; sha256: string; content: string } | null;
  revision_notes: { at: string; feedback: string; stage?: string; decision?: string }[];
  work_items: { id: number; title: string; status: WorkStatus; domain: string }[];
  events: WorkflowEvent[];
  created_at: string;
  updated_at: string;
}

export interface WorkflowSummary {
  id: number;
  project_id: number;
  title: string;
  status: WorkflowDetail["status"];
  template: string;
  current_stage: string;
  current_stage_label: string;
  running: boolean;
  open_question: { id: number; question: string } | null;
  updated_at: string;
}

export interface WorkflowTemplate {
  id: string;
  label: string;
  description: string;
  topics: { id: string; question: string; required: boolean }[];
  research_questions: string[];
}

export type WorkStatus = "proposed" | "todo" | "in_progress" | "awaiting_approval" | "blocked" | "done" | "failed" | "cancelled";

export interface WorkItem {
  id: number;
  project_id: number;
  workflow_id: number | null;
  title: string;
  description: string;
  domain: string;
  executor: "manual" | "command" | "artifact";
  status: WorkStatus;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  depends_on: number[];
  requires_approval: boolean;
  action_id: number | null;
  source: "user" | "template" | "ai";
  error: string | null;
  position: number;
  created_at: string;
  updated_at: string;
}

export interface WorkEvent {
  id: number;
  work_item_id: number | null;
  workflow_id: number | null;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface Suggestion {
  kind: "approval" | "decision" | "question" | "continue" | "recover" | "configure" | "start" | "review";
  priority: number;
  title: string;
  detail: string;
  target: { type: "approvals" | "workflow" | "work" | "changes"; id?: number; project_id?: number };
}

// ---- 0.3: preferences, voice, capabilities, attachments ---------------------------------------------------
export type ThemePref = "system" | "light" | "dark";
export type MotionPref = "system" | "reduce" | "full";
export type SttEngine = "browser" | "pocketsphinx" | "vosk";
export type InteractionMode = "collaborative" | "quiet" | "step_by_step";

export interface VoicePrefs {
  wake_enabled: boolean;
  wake_phrase: string;
  sensitivity: "low" | "medium" | "high";
  stt_engine: SttEngine;
  tts_voice: string | null;
  speak_replies: boolean;
  acknowledge: boolean;
  chime: boolean;
  follow_up: boolean;
  lang: string;
}

export interface Preferences {
  theme: ThemePref;
  motion: MotionPref;
  suggestions: boolean;
  interaction_mode: InteractionMode;
  voice: VoicePrefs;
  wake_consent_at: string | null;
}

export interface PreferencesUpdate {
  theme?: ThemePref;
  motion?: MotionPref;
  suggestions?: boolean;
  interaction_mode?: InteractionMode;
  voice?: Partial<VoicePrefs>;
  wake_consent?: boolean;
}

export interface EngineStatus {
  id: string;
  label: string;
  available: boolean;
  local: boolean;
  reason: string | null;
  note: string | null;
}

export interface VoiceCapabilities {
  enabled: boolean;
  wake: EngineStatus;
  stt_engines: EngineStatus[];
  remote_audio_allowed: boolean;
  sensitivities: string[];
}

export interface PhraseCheck {
  phrase: string;
  valid: boolean;
  unknown_words: string[];
  message: string | null;
}

export interface LocalToolResult {
  status: ActionStatus;
  message: string;
  data: Record<string, unknown>;
  duration_ms: number;
}

export interface SpotifyStatus {
  configured: boolean;
  connected: boolean;
  reason: string | null;
}

export interface Capability {
  id: string;
  label: string;
  description: string;
  status: "available" | "limited" | "unavailable";
  reason: string | null;
  implementation: string;
  entry: string;
  requires: string[];
}

export interface ConversationMode {
  id: string;
  label: string;
  prompt: string;
}

export interface Attachment {
  id: number;
  filename: string;
  mime: string;
  size: number;
}
