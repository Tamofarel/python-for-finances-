# Security, permission model and threat model

NOVA is designed for **one owner on their own computer**, listening on `127.0.0.1`. It has not been
penetration-tested. Automated tests cover the controls below; they do not prove the system is secure.

## Permission model

| Category | Actions | Enforcement (server-side) |
|---|---|---|
| **A — read-only** | list folders, read text files, summarise a project, search history/memory | Allowed for the owner, only inside the project workspace; sensitive files blocked. |
| **B — reversible** | create files/folders, propose edits, export artifacts, apply proposals | Existing files change only via a `ChangeProposal`. Applying requires the exact `proposed_sha256` and fails if the file changed since the proposal (`stale_base`). The original is kept for revert. |
| **C — consequential** | run commands, delete files | An `ActionRequest` stores the parameters and `params_hash = sha256(kind, project, params)`. Approval must send that hash and `confirm: true`; it is single-use, expires after 10 minutes, and the stored parameters are re-hashed before execution (tampering is refused). Commands are re-validated against the allowlist at execution time. |

The UI shows what will happen, why, what is affected, whether it is reversible, the category, and the observed
result. The UI is never the only check: every rule above lives in the backend and is covered by tests in
`backend/tests/test_actions.py` and `test_changes.py`.

## Controls

**Authentication**
- First-run setup requires a random 8-digit code printed to the backend console; setup is refused once an
  owner exists. No default account exists.
- Passwords: Argon2id (`argon2-cffi` defaults), minimum 10 characters. Unknown users get the same error and
  similar timing (dummy verification).
- Sessions: 256-bit random token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` when
  `NOVA_COOKIE_SECURE=true`); only its SHA-256 digest is stored; expiry `NOVA_SESSION_TTL_HOURS`; logout
  deletes the server session; password change revokes all sessions.
- Rate limits: 10 login attempts per 5 minutes per IP+username; 10 setup attempts per 5 minutes per IP.

**Request integrity**
- CSRF: every POST/PUT/PATCH/DELETE from an authenticated session needs `X-CSRF-Token` equal to the session's
  token (constant-time compare). All unsafe `/api` requests also need `X-NOVA-Client: web`, which a cross-site
  form cannot send and which triggers a CORS preflight limited to `NOVA_CORS_ORIGINS`.
- Pydantic validation with explicit length limits on every input; 2 MB request cap; 1 MB file cap.
- Security headers: strict CSP (`script-src 'self'`, no inline scripts, `frame-ancestors 'none'`),
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (camera and
  geolocation off; microphone same-origin only), `Cache-Control: no-store` on API responses.

**Files**
- Client and model paths are always relative to the workspace. Absolute paths, drive letters, `~`, `..`, NUL
  bytes and symlinks/junctions resolving outside the workspace are rejected (`path_rejected`).
- Sensitive names are blocked and hidden: `.env*` (except `*.example`), `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `id_rsa*`, `id_ed25519*`, `*.kdbx`, `.npmrc`, `.pypirc`, `.netrc`, `credentials*`, `*secret*`, `.git`, `.ssh`, `.aws`.
- Writes are atomic (temp file + replace). Only empty folders can be deleted. Binary files are not opened.
- Project workspaces must be inside `NOVA_DATA_DIR/workspaces` or `NOVA_ALLOWED_WORKSPACE_ROOTS`.

**Commands**
- No shell; argument arrays only; forbidden characters `| & ; < > $ \` newline NUL`; no `..`, no absolute paths.
- Prefix allowlist (`NOVA_COMMAND_ALLOWLIST`); `python` resolves to NOVA's own interpreter.
- Working directory = workspace; environment reduced to PATH/system variables (no `NOVA_*`, no API keys);
  stdin closed; timeout (default 300 s); output capped (200 KB); cancellable; results and audit events recorded.

**Secrets**
- API keys only come from the environment / `.env`; they are never stored in the database, returned by the API,
  sent to the browser or passed to subprocesses. Logs and audit details are redacted by key name
  (`password`, `token`, `secret`, `api_key`, `cookie`, `csrf`, `authorization`) and by pattern (`sk-ant-…`,
  `Bearer …`).
- The release packager refuses to include `.env`, databases, logs, `node_modules`, virtualenvs and caches, and
  scans text files for key-like strings.

**Model output**
- Treated as untrusted: rendered as Markdown without raw HTML (`react-markdown`, unsafe URLs dropped), links open
  with `rel="noopener noreferrer"`, Mermaid runs with `securityLevel: "strict"`.
- AI change proposals only target the file the user chose; the model never selects paths or triggers actions.
- Retrieved content (files, web snippets) is fenced in `<untrusted_data>` blocks.

## Threat model

**Assets:** workspace files, conversation history and memory, provider API keys, the ability to run commands.

| Threat | Mitigation | Residual risk |
|---|---|---|
| Another local OS user or malware on the same machine | Bound to 127.0.0.1; auth required for everything | Malware running as you can read `data/nova.db` and `.env` directly. Use OS account security / disk encryption. |
| A malicious website driving your browser (CSRF, DNS rebinding) | SameSite=Strict cookie, CSRF token, custom header + restricted CORS | A rebinding attack still lacks the session cookie; keep the default binding. |
| Someone on your network (if you bind to 0.0.0.0) | Auth, rate limiting, warning at startup | No TLS built in. Put a reverse proxy with HTTPS in front and set `NOVA_COOKIE_SECURE=true`. |
| Racing the first-run setup | Console-only setup code | Anyone who can read your terminal could use the code; complete setup right after installing. |
| Prompt injection in files or web pages | Untrusted-data fencing; no autonomous actions; path chosen by user; approvals | The model can still give bad advice or propose a harmful diff — review diffs before applying. |
| Malicious project scripts (`npm test`, `pytest` plugins) | Explicit approval per run, allowlist, minimal env, timeout | An approved test command runs the project's own code with your user rights. Only approve for trusted projects. |
| Path traversal / symlink escape | Normalisation + resolved-path containment | — |
| Secret leakage via logs/audit/UI | Redaction, no secret fields in DB/API | Secrets you paste into chat are stored as messages and sent to the selected provider. |
| Data sent to a cloud provider | Provider shown in the top bar; local Ollama option | Anthropic receives the conversation, memory and project instructions in the system prompt. |
| Browser speech recognition | Push-to-talk only, disclosed in UI | Chrome/Edge send audio to the vendor's service. |
| Lost password | — | Recovery = delete the database (documented). |

## Reporting

This is a personal project. Open an issue or contact the owner privately for security problems.

## 0.2: workflows, tasks and voice

- **Model output stays untrusted.** AI questions and AI task lists are parsed as JSON and validated with Pydantic schemas;
  invalid output falls back to the template and is logged as an event. AI-generated tasks are always `manual`: a model can
  never create a command task, choose arguments, or trigger an action. Command tasks are created only by the owner, with
  arguments validated against the allowlist at creation and again at execution.
- **Approvals are unchanged.** Command tasks create ordinary `ActionRequest`s (Category C, parameter hash, single use,
  1-hour expiry). Rendering a model response never triggers anything.
- **Proposal approval** is bound to the SHA-256 of the proposal document the owner reviewed; any edit invalidates it.
- **Research content** from SearXNG is stored as data and fenced as `<untrusted_data>` in prompts. Pending research is
  never filled with invented sources.
- **Ownership** checks cover workflows, questions, work items, events and suggestions (other users get 404).
- **Voice (0.2)**: the microphone opened only while push-to-talk was active. 0.3 adds an opt-in wake phrase, see below.
- **Audit** events: `workflow.created`, `workflow.stage_started`, `workflow.approve|revise|reject` (including denied
  attempts), `workflow.cancelled`, `work.created|updated|started|cancelled|deleted`, plus the existing action events.

## 0.3: wake phrase, local audio, images, preferences

- **Wake phrase is opt-in and local.** Off by default. Turning it on the first time requires accepting a notice that
  explains that the microphone stays open and that audio goes to NOVA's backend on this computer only; the backend
  refuses to enable it without `wake_consent: true` and stores the consent time. Wake detection runs in the backend
  (PocketSphinx); raw audio is never sent to a remote service to look for the wake phrase.
- **Voice WebSocket hardening** (`/api/voice/stream`): accepted only from loopback clients unless
  `NOVA_VOICE_ALLOW_REMOTE=true`; Origin must be NOVA's own origin (or a configured CORS origin); a valid session
  cookie (SameSite=Strict) is required; the first message must carry the session's CSRF token (constant-time compare);
  frames over 32 kB, wrong sample rates, unknown phrases and unavailable engines are refused; utterances are capped
  (`NOVA_VOICE_MAX_UTTERANCE_SECONDS`). Decoding runs in a worker thread so a stream cannot block the event loop.
- **No audio retention.** Audio exists only in memory for the current utterance. Transcripts are stored only when they
  become messages, like typed text. Audit events `voice.stream_started` / `voice.stream_ended` record mode, engine,
  duration and counts — never audio or words; `voice.wake_enabled` / `voice.wake_disabled` record the switch.
- **Indicators and control**: the sidebar microphone button shows the real state and closes the microphone in one click;
  signing out releases it; `Permissions-Policy: microphone=(self), camera=()`.
- **Browser speech recognition** is used only after the wake phrase or while *Talk* is held, and only if the owner chose
  it; the notice and Settings say that Chrome/Edge send that audio to Google/Microsoft. Online speech-synthesis voices
  are labelled and never chosen by default.
- **Voice cannot approve.** Spoken commands only answer, skip or continue an idea's questions; approvals require the
  on-screen confirmation of the exact action, as before.
- **Images** are checked by content (magic bytes), limited to 5 MB and 4 per message, stored under
  `data/uploads/<owner id>/` with random names, served only to their owner with `nosniff` and private caching, re-sent to
  the model only for the last messages, and deleted with their conversation. `img-src` allows `blob:` only for local
  previews.
- **Preferences** are per owner and validated server-side (theme, motion, suggestion and voice settings); they cannot
  change permissions.
- **Suggestions are bounded**: computed from the owner's own data while the command center is open; NOVA does not poll
  external systems or act on a suggestion by itself.

## 0.4: explicit tools and Spotify

- The local tool catalog contains only registered schemas. Tool execution requires the normal owner session, CSRF
  token and `X-NOVA-Client: web`; malformed arguments, unknown tools, disabled tools and approval-level actions are
  refused. Tool audit records store the tool name, outcome and duration, not query arguments.
- Desktop tools have their own opt-in `NOVA_DESKTOP_CONTROL_ENABLED` flag and are only registered on Windows. The
  Spotify adapter takes a fixed action enum and bounded search query. It never accepts a command line, file path or
  arbitrary executable and never invokes a shell. The adapter currently runs in the NOVA backend process under the
  same Windows account; it is not an OS-level sandbox or restricted-token service.
- Spotify uses Authorization Code with PKCE and a one-use state generated from an authenticated connection request.
  It requests playback-state, playback-control and private-playlist-read scopes. Passwords and client secrets are not
  requested or stored. The client ID is public application configuration; access and refresh tokens are encrypted
  with Windows DPAPI for the current OS account and are never returned to the browser or written to logs/audit.
- Spotify search and playback requests are sent to Spotify only after the owner connects the account. NOVA reports
  successful playback only after reading a matching playback state. Generic Windows URI/media-key fallback results
  are marked unverified. Tokens in the local data folder remain accessible to malware running as the same Windows
  user, consistent with the existing local-first threat model.
- Spotify HTTP calls use a request timeout bounded by the tool's deadline (0.4.1 adds cancellation and per-tool
  deadlines to the registry).
- This increment did not add model-driven tool selection, generic operation cancellation, or a separate low-privilege
  Windows service. 0.4.1 adds the first two (below); the third is a documented decision.

## 0.4.1: model-driven actions

- **Only registered tools.** The model can only *request* registered tools. Every call is checked before it runs:
  - the tool is known and enabled;
  - the arguments match its schema (with `extra="forbid"`);
  - it is grounded in the owner's request;
  - it is authorized for its risk class;
  - device control is not paused by the emergency stop.

  There is no shell tool. Commands only become approval requests for the existing allowlisted runner.
- **Safety-critical calls are never executed from chat or voice.** This covers project commands, locks, covers,
  climate, water heaters, valves, sirens, humidifiers and switches by default. Each becomes a `tool_call` (or
  `run_command`) approval request bound to a hash of its exact parameters. It runs once, after on-screen confirmation,
  within 10 minutes, and re-checks pause and enablement. The emergency stop rejects pending device requests.
- **Prompt injection.** Tool results and untrusted data go back to the model as data, and the system prompt forbids
  acting on instructions inside `<untrusted_data>`. Independently of the model:
  - tools are offered only when the owner's message asks NOVA to act;
  - a call must be grounded in the owner's own words;
  - websites are limited to an owner-defined host allowlist (no credentials in URLs);
  - files are confined to the attached project, and executable or script types are never opened.

  Together these limit what an injected instruction could make NOVA do.
- **Bounded execution.** Each request is limited to 5 model rounds, 6 tool calls, at most 2 identical calls, and
  240 s overall. Each tool has a deadline enforced from outside its thread. Stop, disconnect, barge-in and
  `POST /api/actions/emergency-stop` cancel running tools cooperatively.
- **Honest outcomes.** Statuses come from the integration, not the model: Spotify playback is reported only from a
  matching player state, and Home Assistant changes only from the reported device state. When an action does not
  succeed, NOVA writes the reply itself.
- **Audit.** Each execution records the tool, outcome, duration, risk, whether it was verified, and the conversation
  ID — never the request text or arguments. Emergency stop, resume, approvals and refused ungrounded calls are audited.
- **Home Assistant.** The long-lived token lives in `.env` (like the Anthropic key) and is sent only to
  `NOVA_HOME_ASSISTANT_URL`. Only entities listed in `NOVA_HOME_ASSISTANT_ENTITIES` are reachable. Locks, covers,
  climate and the other safety-critical classes cannot be relaxed. Thermostat set points are limited to 5–30 °C.
- **No separate Windows agent.** Windows actions run in the backend under the owner's Windows account; a separate
  process under the same account would add no isolation. The boundary is the typed tool set and its allowlists. This
  is not an OS sandbox: malware running as the same user is out of scope, as before.
