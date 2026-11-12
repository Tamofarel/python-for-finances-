# Changelog

## Unreleased — 2026-11-12

Details: [docs/ACTIONS.md](docs/ACTIONS.md#spotify).

### Fixed
- **“Play X” was accepted by Spotify but never started** (“Spotify accepted the request, but NOVA could not confirm…”).
  The Spotify desktop app answered 204 to a plain track request and left the player empty, and an idle device ignored
  play requests. NOVA now transfers playback to an idle device first, starts the track inside its album, and falls
  back to the plain request once.
- **“Open Word and report what we did today” only opened Word.** No tool could write a document, and one succeeded
  action ended the request. NOVA now reads today's records, writes and saves a Word document, reads the saved file
  back, shows it in Word and reports the file's location. Details: [docs/ACTIONS.md](docs/ACTIONS.md#word-documents-and-multi-step-tasks).

### Changed
- Spotify playback no longer needs `NOVA_DESKTOP_CONTROL_ENABLED`. With desktop control off, NOVA uses the Spotify
  Web API only: it never starts the Spotify app and never sends media keys.
- Playback scopes are checked before calling Spotify; a connection without them asks you to reconnect.
- A failed confirmation now says what Spotify reported (nothing playing, or another track).

### Added
- `word_document` tool (create, open, read, status) and `activity_today` tool. New dependency: `python-docx`.
- Multi-step task plans and a `task` stream event with `TASK_IN_PROGRESS`, `TASK_COMPLETED`,
  `TASK_PARTIALLY_COMPLETED` and `TASK_FAILED`; succeeded actions carry `completion: "ACTION_COMPLETED"`.
- `NOVA_DOCUMENTS_DIR`: the folder for Word documents (default `Documents\NOVA`).
- `spotify_control` action `now_playing` (“what song is currently playing?”).
- `data.outcome` on Spotify results (`SUCCESS`, `VERIFICATION_TIMEOUT`, `PLAYBACK_FAILED`, `NO_DEVICE`, …).
- Direct routing of unmistakable Spotify requests (controls, “play *title* by *artist*”, “play my *name* playlist”).

## 0.4.1 — 2026-10-18

NOVA now carries out requests to act instead of explaining them. Diagnosis, design and the live test record:
[docs/ACTIONS.md](docs/ACTIONS.md).

### Fixed
- **Requests to act were answered with instructions.** Four causes combined:
  - the model was never offered tools;
  - the system prompt said it could not act;
  - Spotify was reachable only through a voice-only pattern match that “open Spotify and play X” never matched;
  - desktop control was off.
- **The Spotify tool itself was broken:**
  - the fallback path crashed with `UnboundLocalError`;
  - `play_pause` was not handled;
  - playback was checked only once, right after the play request, so successful plays were reported as failed;
  - `.json()` was called on empty (HTTP 204) responses;
  - “Premium required” and “no device” were not told apart;
  - it played the first search result without asking.
- **Device actions shown as done in Approvals.** The approval dialog said “Done” for any result; device results now
  show their real status.

### Added
- **Model-driven tool orchestration** in the conversation pipeline. It works with Ollama tool calling (llama3.2,
  mistral) and Claude `tool_use`, and falls back honestly for models without tools. The loop is bounded (rounds,
  calls, repeats, deadlines). Stop, barge-in and disconnect cancel running tools. Results stream as `action` events and
  are saved on the message.
- **Upgraded registry:**
  - risk classes (observe, reversible, safety-critical) with per-call classification;
  - `ToolContext` (deadline, cancellation, pause, project workspace);
  - honest statuses (`unverified`, `needs_input`, `needs_authorization`, `awaiting_approval`, `cancelled`).

  Safety-critical calls become one-use `tool_call` approvals, never run by voice.
- **Spotify:**
  - search and matching robust to accents, swapped title and artist, and misremembered titles;
  - asks you to choose when unsure, and plays your choice by exact ID;
  - opens the app and waits for its device;
  - polled verification, precise blockers, bounded retries, OAuth reconnect link.
- **Windows tools:** approved apps (`NOVA_DESKTOP_APPS`), allowed websites (`NOVA_BROWSER_ALLOWED_HOSTS`), project
  files (no executables), the project in VS Code or Explorer, system media keys, and project commands through approval.
- **Home Assistant integration** for smart-home and physical devices:
  - an entity allowlist and risk per device type (locks, covers, climate and the like need approval);
  - verification from the reported state;
  - safe temperature bounds.
- **Emergency stop** (Settings → Actions, `POST /api/actions/emergency-stop`): cancels running actions and commands,
  rejects pending device approvals, and pauses control until resumed.
- **Safeguards for small local models**, each found in live tests with llama3.2:
  - intent gate;
  - grounding check;
  - choice resolution;
  - recovery of tool calls written as text;
  - NOVA reports failures itself instead of letting the model paraphrase them.
- **UI:** action cards in replies (status, verification, *Connect Spotify*, *Review in Approvals*, choices); the voice
  layer speaks verified results; a Settings → Actions tab; an “Actions” capability.
- Migration `0004` (`messages.actions`, nullable `action_requests.project_id`). Backend tests 171 → 279; frontend
  tests 74 → 78 (2 obsolete pattern-matcher tests removed).

### Removed
- The client-side Spotify pattern matcher (`spotifyVoiceCommand`). Typed and spoken requests now share one pipeline.

## 0.3.0 — 2026-10-17

Incremental evolution of 0.2.0 (no rewrite). Details: docs/EVOLUTION.md, docs/VOICE.md.

### Added
- Light, dark and system themes (a designed silver/white light palette, not an inversion), reduced-motion preference
  (system/reduce/full), both saved per owner and cached by the browser; WCAG AA contrast enforced by a test.
- Local wake phrase: PocketSphinx keyphrase spotting in the backend over a hardened WebSocket (loopback-only by default,
  Origin, session, CSRF), configurable phrase with dictionary check, three sensitivities, chime, consent before the first
  use, microphone status and one-click off in the sidebar, recoverable permission errors, audit of start/stop only.
- Local transcription (PocketSphinx; optional Vosk with a downloaded model) besides browser recognition; local
  push-to-talk; follow-up listening; barge-in by wake phrase; voice selection preferring on-device voices.
- Assistant state machine v2: wake detected, understanding (request sent), thinking (model accepted), awaiting your
  answer, cancelled; automatic return to standby for transient states; errors stay until dismissed.
- Command center redesign: calm centered stage (core, state line, captions, one dock for text, voice and images), up to
  three suggestions, side panels only when relevant (approvals, tasks in motion, the idea's documents, long replies,
  project), compact icon rail with tooltips, mobile drawer.
- Ideas from a sentence: idea detection (EN/FR) on the command center, automatic template choice with its reason,
  intent confirmation as the first question (AI summary or faithful restatement), questions answered in place, spoken
  questions and commands (skip, continue, stop, yes), "Not an idea?" escape, skip stage, explore everything (run all
  stages up to the proposal). The image-recognition template asks about users before scope and covers offline use and
  running cost.
- Image and screenshot attachments for vision-capable models (Claude, Ollama vision models), with honest refusal for
  models that cannot read images; per-conversation focus modes (engineering, research, product, design, business,
  learning); capability report from live checks; approval-gated move/rename of workspace files.
- Settings sections: Appearance, Voice, Suggestions, Capabilities, Account and security.
- Migration `0003`; backend tests 122 → 162; frontend tests 40 → 71; `scripts/dev/browser_e2e.py` (real Chromium with a
  fake microphone) and `scripts/dev/make_voice_fixtures.sh`.

### Changed
- Spoken replies are now for voice requests (typed messages are not read aloud automatically; *Read aloud* remains).
- The floating panels of 0.2 (project context, ideas form, next steps, approvals, tasks, research) are replaced by
  contextual panels; the activity side panel on other screens is off by default.
- Ideas page defaults to "Let NOVA choose" the kind of idea.

### Fixed
- A command that was still starting when NOVA shut down (or when its task was cancelled) could keep running; the
  runner now refuses to start and kills it. This made the existing test `test_shutdown_stops_running_commands`
  intermittently fail.
- Preferences loaded from the server could overwrite a change made while the page was loading.

## 0.2.0 — 2026-10-17

Incremental evolution of 0.1.0 (no rewrite). Details: docs/EVOLUTION.md.

### Added
- Holographic command center as the home screen: three.js core (lazy-loaded) with an SVG fallback when WebGL is
  unavailable, floating panels (project context, ideas, next steps, approvals, tasks, research and documents),
  compact navigation rail, responsive layout, reduced-motion support.
- Assistant state machine with an explicit transition table (standby, listening, transcribing, thinking, responding,
  speaking, researching, designing, waiting for input, awaiting approval, executing, interrupted, error), driven only
  by real events and observed backend state.
- Voice conversation through the existing chat pipeline: push-to-talk, live transcript, microphone waveform, spoken
  streaming replies, short acknowledgement, barge-in, immediate interruption (button or Esc), continue after
  interruption; speech input/output adapters for future local engines.
- Persistent idea-to-product workflows with six templates, one question at a time, honest research (sourced or
  pending), editable documents (feasibility, architecture, data model, API contract, UI flows, technology choices,
  PRD, user stories, implementation plan, risk register), consolidated proposal, hash-bound approval, revision and
  rejection, implementation tasks; background stage runner with cancellation and restart recovery.
- Plant-health photo app example (UI button, `scripts/demo_plant_health.py`, `docs/examples/plant-health-answers.json`).
- Task engine: work items with domains, dependencies, inputs/outputs, events, errors, approval-gated command tasks
  and AI document tasks; next-step suggestions; project Work board.
- Migration `0002`; 28 new backend tests (122 total) and 21 new frontend tests (40 total); smoke test extended to 22 steps.

### Changed
- The full chat moved from `/` to `/chat`; the chat screen uses the shared voice controller (the old `useVoice` hook was removed).
- Project "Tasks" tab renamed "Commands"; new "Work" tab.

## 0.1.0 — 2026-10-17

First release, built from an empty project root (no existing NOVA repository was available to reuse).

### Added
- FastAPI backend with SQLite, SQLAlchemy 2, Alembic migration `0001_initial_schema`, structured JSON logging
  with secret redaction, consistent error format, health/readiness endpoints, graceful shutdown and recovery
  of interrupted work.
- Local accounts: console setup code for first run, Argon2id passwords, server-side sessions, CSRF tokens,
  login/setup rate limiting, password change with session revocation, audit trail.
- Provider abstraction with Ollama (HTTP API) and Anthropic (official SDK) providers, live status checks,
  model selection, honest error reporting, SSE streaming, cancellation and retry.
- Conversations: create, rename, search, delete, persistent history, project attachment.
- Projects with confined workspaces, file explorer, reviewable change proposals with unified diffs,
  hash-bound apply, stale detection, revert; AI-proposed changes for a user-chosen file; project summaries.
- Idea analysis and design artifacts (requirements, user stories, architecture, API contract, data model,
  UI spec, component inventory, implementation plan, review summary) with Mermaid rendering and export.
- Approval system for consequential actions; allowlisted, shell-free command runner with timeout, output
  cap, minimal environment and cancellation; delete-with-approval.
- Explicit memory (preferences, facts, notes; general or per project) with search, edit, disable, clear.
- SearXNG research provider with source lists and briefs; disabled state reported honestly.
- React + TypeScript UI: setup/login, assistant workspace, history, models, projects (overview, files,
  changes, ideas, design, tasks, activity), approvals, memory, settings/permissions/audit; activity panel;
  push-to-talk voice and spoken replies; responsive layout, keyboard focus, reduced motion.
- Tests: 94 backend tests, 19 frontend tests, end-to-end smoke test; Windows and Unix setup scripts;
  release packager with secret scan and archive verification.
