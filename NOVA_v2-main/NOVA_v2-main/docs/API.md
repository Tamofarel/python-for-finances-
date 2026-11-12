# NOVA API reference

Interactive docs are served by the backend at `/api/docs` (Swagger UI) and the machine-readable spec at
`/api/openapi.json` (a snapshot is in `docs/openapi.json`).

## Conventions

- **Base URL**: same origin as the UI (`http://127.0.0.1:8000`). All endpoints are under `/api`.
- **Authentication**: an HttpOnly, `SameSite=Strict` session cookie (`nova_session`) set by `/api/auth/setup` or `/api/auth/login`.
- **State-changing requests** (POST/PUT/PATCH/DELETE) must send `X-NOVA-Client: web` and, when authenticated, `X-CSRF-Token: <csrf_token>` (returned by `/api/auth/status`, `/login`, `/setup`).
- **Errors** always look like `{"error": {"code": "…", "message": "…", "details": …}}`. Common codes: `unauthorized` (401), `csrf_failed` / `forbidden` / `path_rejected` / `command_rejected` / `approval_mismatch` (403), `not_found` (404; also used for resources owned by someone else), `conflict` / `stale_base` / `not_pending` (409), `validation_error` (422), `rate_limited` (429), `provider_error` (502), `research_unavailable` (503).
- **Streaming**: `POST /api/conversations/{id}/messages` and `/retry` return `text/event-stream` with events `start`, `delta` (`{text}`), `error` (`{message, kind, retryable}`) and `done` (`{message}`). Closing the connection cancels generation; the partial answer is stored with status `cancelled`.
- **Approvals**: `POST /api/actions/{id}/approve` requires `{"params_hash": <hash shown in the request>, "confirm": true}`; `POST …/changes/{id}/apply` requires `{"proposed_sha256": …}`. Approvals are single-use.

### 0.2 additions

- **Workflows**: `POST /api/workflows` starts one (creating a project when `project_id` is omitted) and immediately asks the first question. Stage runs return at once and continue in the background (`running: true`); poll `GET /api/workflows/{id}`. `?wait=true` blocks until the stage finishes (used by scripts and tests). `mode` is `auto` (AI when a live check succeeds, otherwise drafts from your answers), `ai` (fail if unavailable) or `template`.
- **Decision**: `POST /api/workflows/{id}/decision` with `{"decision": "approve", "proposal_sha256": <sha of the reviewed proposal>}`; `revise` needs `feedback` and optionally `reopen_stage`; `reject`.
- **Tasks**: `executor` is `manual`, `command` (`inputs.argv`, validated against the allowlist; starting it creates a normal approval request) or `artifact` (`inputs.kind`, `inputs.prompt`; needs a reachable AI model). Status changes for non-manual tasks follow observed results.

### 0.3 additions

- **Preferences**: `GET /api/preferences`, `PUT /api/preferences` (partial: `theme`, `motion`, `suggestions`, `voice{…}`).
  Turning `voice.wake_enabled` on the first time requires `"wake_consent": true` (error `consent_required`); the wake phrase
  is validated against the detector's dictionary (`invalid_wake_phrase`).
- **Voice**: `GET /api/voice/capabilities` (wake detector and transcribers with live availability and the reason when
  unavailable), `POST /api/voice/check-phrase`. **WebSocket** `/api/voice/stream`: same-origin, loopback-only unless
  `NOVA_VOICE_ALLOW_REMOTE=true`, session cookie required. First message (text):
  `{"type":"hello","csrf":"…","mode":"wake"|"utterance","stt":"browser"|"pocketsphinx"|"vosk","phrase":"hey nova","sensitivity":"medium","sample_rate":16000}`.
  Then binary frames of 16 kHz mono PCM16 (≤ 32 000 bytes each) and control messages `pause`, `resume`, `listen`
  (`{"ptt": true}` for push-to-talk), `end`, `cancel`, `close`. Server events: `ready`, `wake`, `listening`, `speech`,
  `partial`, `transcribing`, `final` (`text`), `empty` (`reason`), `error` (`code`, `message`). Refusals send an `error`
  event and close with 4401/4403/4400. Audio is never stored.
- **Attachments**: `POST /api/conversations/{id}/attachments` (multipart `file`; PNG/JPEG/GIF/WebP by content, ≤ 5 MB),
  then send `attachment_ids` (≤ 4) with the message. `GET /api/attachments/{id}` serves the owner's image. A model that
  reports it cannot read images gets `vision_unsupported` (409) instead of a silent text-only answer.
- **Capabilities and modes**: `GET /api/capabilities` (live status per domain), `GET /api/modes`; conversations accept
  `mode` on create and update.
- **Workflows**: `template: "auto"` picks a template from the idea (`GET /api/workflow-templates/suggest?idea=…` shows
  which and why); the first question confirms the restated idea (topic `intent`); `POST …/stages/{stage}/skip`,
  `POST …/continue?all=true` (run every remaining stage up to the proposal), `POST …/dismiss` (archive an idea that was
  not one, with its auto-created project).
- **Files**: `POST /api/projects/{id}/actions/move` (`src`, `dst`) creates a category-B approval request; nothing is
  overwritten.

### 0.4.1 additions (actions; see [ACTIONS.md](ACTIONS.md))

- **Conversation stream.** `POST /api/conversations/{id}/messages` and `…/retry` may now emit `action` events between
  `start` and `done`, each sent twice:
  - first `{"id","phase":"started","tool","arguments","risk"}`;
  - then the same `id` with `"phase":"finished"` plus `status`, `message`, `verified`, `data` and `duration_ms`.

  `status` is one of `succeeded`, `unverified`, `needs_input`, `needs_authorization`, `awaiting_approval`,
  `unavailable`, `denied`, `failed`, `timed_out`, `cancelled`. `data` may carry `connect_url`, `candidates`,
  `action_id`, `blocker`, `device`, `track`. Messages now have `actions` (the finished records, or `null`).
- **Tools.**
  - `GET /api/tools` lists every registered tool with `risk` (`observe`, `reversible`, `safety-critical`), `enabled`,
    `unavailable_reason`, `timeout_s` and the JSON Schema of its arguments.
  - `POST /api/tools/execute` (`{"name","arguments"}`) runs one routine tool directly. Safety-critical tools are
    refused there, and every call respects the emergency stop.
- **Device approvals.** Safety-critical calls create an approval request with `kind: "tool_call"`, `project_id: null`
  and `params: {"tool","arguments"}`. `POST /api/actions/{id}/approve` with the usual `params_hash` and
  `confirm: true` runs it once and returns `result: {"status","message","data"}`.
- **Emergency stop.**
  - `GET /api/actions/control` → `{"paused","running"}`.
  - `POST /api/actions/emergency-stop` cancels running actions and commands, rejects pending `tool_call` requests and
    pauses control. It returns the counts.
  - `POST /api/actions/resume` lifts the pause.

  All three need the owner session; the two POSTs also need CSRF.

## Endpoints

| Area | Method | Path | Summary |
|---|---|---|---|
| actions | GET | `/api/actions` | List Actions |
| actions | GET | `/api/actions/control` | Action Control (emergency-stop state, running actions) |
| actions | POST | `/api/actions/emergency-stop` | Emergency Stop |
| actions | POST | `/api/actions/resume` | Resume Actions |
| actions | GET | `/api/tasks` | List Tasks |
| actions | GET | `/api/tasks/{task_id}` | Get Task |
| actions | POST | `/api/actions/{action_id}/approve` | Approve Action |
| actions | POST | `/api/actions/{action_id}/reject` | Reject Action |
| actions | POST | `/api/projects/{project_id}/actions/command` | Request Command |
| actions | POST | `/api/projects/{project_id}/actions/delete` | Request Delete |
| actions | POST | `/api/projects/{project_id}/actions/move` | Request Move |
| actions | POST | `/api/tasks/{task_id}/cancel` | Cancel Task |
| artifacts | DELETE | `/api/projects/{project_id}/artifacts/{artifact_id}` | Delete Artifact |
| artifacts | GET | `/api/projects/{project_id}/artifacts` | List Artifacts |
| artifacts | GET | `/api/projects/{project_id}/artifacts/{artifact_id}` | Get Artifact |
| artifacts | PATCH | `/api/projects/{project_id}/artifacts/{artifact_id}` | Update Artifact |
| artifacts | POST | `/api/projects/{project_id}/artifacts` | Create Artifact |
| artifacts | POST | `/api/projects/{project_id}/artifacts/generate` | Generate Artifact |
| artifacts | POST | `/api/projects/{project_id}/artifacts/{artifact_id}/export` | Export Artifact |
| auth | GET | `/api/auth/status` | Status |
| auth | POST | `/api/auth/login` | Login |
| auth | POST | `/api/auth/logout` | Logout |
| auth | POST | `/api/auth/password` | Change Password |
| auth | POST | `/api/auth/setup` | Setup |
| conversations | DELETE | `/api/conversations/{conv_id}` | Delete Conversation |
| conversations | GET | `/api/attachments/{attachment_id}` | Get Attachment |
| conversations | GET | `/api/conversations` | List Conversations |
| conversations | GET | `/api/conversations/{conv_id}` | Get Conversation |
| conversations | PATCH | `/api/conversations/{conv_id}` | Update Conversation |
| conversations | POST | `/api/conversations` | Create Conversation |
| conversations | POST | `/api/conversations/{conv_id}/attachments` | Upload Attachment |
| conversations | POST | `/api/conversations/{conv_id}/messages` | Send Message |
| conversations | POST | `/api/conversations/{conv_id}/retry` | Retry |
| files | GET | `/api/projects/{project_id}/changes` | List Changes |
| files | GET | `/api/projects/{project_id}/changes/{change_id}` | Get Change |
| files | GET | `/api/projects/{project_id}/files` | List Files |
| files | GET | `/api/projects/{project_id}/files/content` | Read File |
| files | POST | `/api/projects/{project_id}/assist/summary` | Summarize Project |
| files | POST | `/api/projects/{project_id}/changes` | Propose Change |
| files | POST | `/api/projects/{project_id}/changes/ai` | Propose Ai Change |
| files | POST | `/api/projects/{project_id}/changes/{change_id}/apply` | Apply Change |
| files | POST | `/api/projects/{project_id}/changes/{change_id}/reject` | Reject Change |
| files | POST | `/api/projects/{project_id}/changes/{change_id}/revert` | Revert Change |
| files | POST | `/api/projects/{project_id}/files` | Create Path |
| misc | DELETE | `/api/memory` | Clear Memory |
| misc | DELETE | `/api/memory/{memory_id}` | Delete Memory |
| misc | GET | `/api/audit` | Audit History |
| misc | GET | `/api/capabilities` | Capabilities |
| misc | GET | `/api/health` | Health |
| misc | GET | `/api/memory` | List Memory |
| misc | GET | `/api/modes` | Modes |
| misc | GET | `/api/ready` | Ready |
| misc | GET | `/api/research/status` | Research Status |
| misc | PATCH | `/api/memory/{memory_id}` | Update Memory |
| misc | POST | `/api/memory` | Create Memory |
| misc | POST | `/api/research` | Research |
| projects | DELETE | `/api/projects/{project_id}` | Delete Project |
| projects | GET | `/api/projects` | List Projects |
| projects | GET | `/api/projects/{project_id}` | Get Project |
| projects | GET | `/api/projects/{project_id}/activity` | Project Activity |
| projects | PATCH | `/api/projects/{project_id}` | Update Project |
| projects | POST | `/api/projects` | Create Project |
| providers | GET | `/api/providers` | List Providers |
| providers | PUT | `/api/providers/selection` | Select Provider |
| tools | GET | `/api/tools` | List Tools |
| tools | POST | `/api/tools/execute` | Execute Tool |
| tools | GET | `/api/spotify/status` | Spotify Status |
| tools | GET | `/api/spotify/connect` | Spotify Connect (redirects to Spotify) |
| tools | POST | `/api/spotify/disconnect` | Spotify Disconnect |
| voice | GET | `/api/preferences` | Get Preferences |
| voice | GET | `/api/voice/capabilities` | Voice Capabilities |
| voice | POST | `/api/voice/check-phrase` | Check Phrase |
| voice | PUT | `/api/preferences` | Put Preferences |
| voice | WS | `/api/voice/stream` | Local microphone stream (wake phrase / transcription) |
| work | DELETE | `/api/work/{item_id}` | Delete Work |
| work | GET | `/api/suggestions` | Get Suggestions |
| work | GET | `/api/work` | List Work |
| work | GET | `/api/work/{item_id}` | Get Work |
| work | GET | `/api/work/{item_id}/events` | Work Events |
| work | PATCH | `/api/work/{item_id}` | Update Work |
| work | POST | `/api/projects/{project_id}/work` | Create Work |
| work | POST | `/api/work/{item_id}/cancel` | Cancel Work |
| work | POST | `/api/work/{item_id}/start` | Start Work |
| workflows | GET | `/api/workflow-templates` | List Templates |
| workflows | GET | `/api/workflow-templates/suggest` | Suggest |
| workflows | GET | `/api/workflows` | List Workflows |
| workflows | GET | `/api/workflows/{workflow_id}` | Get Workflow |
| workflows | POST | `/api/workflows` | Create Workflow |
| workflows | POST | `/api/workflows/{workflow_id}/archive` | Archive |
| workflows | POST | `/api/workflows/{workflow_id}/cancel` | Cancel Job |
| workflows | POST | `/api/workflows/{workflow_id}/clarify/complete` | Complete Clarify |
| workflows | POST | `/api/workflows/{workflow_id}/continue` | Continue Workflow |
| workflows | POST | `/api/workflows/{workflow_id}/decision` | Decide |
| workflows | POST | `/api/workflows/{workflow_id}/dismiss` | Dismiss |
| workflows | POST | `/api/workflows/{workflow_id}/questions/next` | Next Question |
| workflows | POST | `/api/workflows/{workflow_id}/questions/{question_id}/answer` | Answer Question |
| workflows | POST | `/api/workflows/{workflow_id}/questions/{question_id}/skip` | Skip Question |
| workflows | POST | `/api/workflows/{workflow_id}/stages/{stage_name}/run` | Run Stage |
| workflows | POST | `/api/workflows/{workflow_id}/stages/{stage_name}/skip` | Skip Stage |