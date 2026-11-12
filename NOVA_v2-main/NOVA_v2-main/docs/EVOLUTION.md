# NOVA evolution

How each release was built on the previous one without a rewrite: the baseline, the integration points, what changed,
and what was deliberately left unchanged. Newest first.

# 0.3: calm holographic interface, local wake phrase, flexible ideas, multi-domain capabilities

## Baseline (observed before any change)

- Source: the released 0.2.0 archive (SHA-256 `4ccce963…`), restored and verified, committed as the baseline; work
  happened on the branch `feature/v0.3-voice-themes-capabilities`.
- Baseline tests: backend **122 passed**, frontend **40 passed**, type check passed.
- Observed problems in 0.2 (from the owner's screenshots and the code): six panels always visible around the core,
  a dark-only palette with hard-coded colours, an "admin dashboard" feel, voice limited to push-to-talk with browser
  recognition, and ideas that had to be started from a form with a template picked by hand.

## Integration points

| Need | Existing piece reused | How |
|---|---|---|
| Themes | CSS tokens in `styles.css` | Every hard-coded colour became a token; a designed light palette (`data-theme="light"`) next to the dark one; the three.js core, waveform, Mermaid and code highlighting read the theme. |
| Wake phrase and local STT | 0.2 voice adapters, chat stream events, state machine | A backend voice session (`app/voice/`) behind a WebSocket; the browser side (`voice/capture.ts`, `voice/localStream.ts`) feeds the same voice store, which still sends transcripts through the one conversation pipeline. |
| States | `assistant/machine.ts` transition table | Extended (wake detected, understanding, awaiting your answer, cancelled) with automatic return to standby for transient states; still only real events. |
| Ideas from a sentence | Workflow engine, templates, `ask_next` | An `intent` question restates the idea first; `template: "auto"`; skip stage, run all, dismiss. The command center hosts the questions in place. |
| Capabilities | Provider registry, research provider, voice engines | `GET /api/capabilities` derives each domain's status from live checks. |
| Images | Conversation pipeline, provider interface | `ChatMessage.images` and per-provider encoding; attachments table and owner-scoped storage. |
| File organisation | Workspace confinement, `ActionRequest` approvals | `move_path` (category B) reuses parameter-bound approvals. |
| Preferences | `app_settings` | One validated `preferences` value per owner (theme, motion, suggestions, voice, wake consent). |

## What changed

**Backend**: `app/voice/` (PocketSphinx wake detector, PocketSphinx/Vosk transcribers, endpointer, session),
`api/voice.py` (preferences, voice capabilities, phrase check, WebSocket), `services/preferences.py`,
`services/capabilities.py`, `services/modes.py`, `services/attachments.py`, provider image support, workflow intent
confirmation, auto template, skip/run-all/dismiss, move/rename approvals, migration `0003`.

**Frontend**: theme and motion preferences (`store/prefs.ts`), redesigned shell (icon rail with tooltips, microphone and
theme controls), command center (core, state line, captions, one dock for text/voice/images, ≤ 3 suggestions, side
panels only when relevant, idea focus with spoken questions and commands), wake-phrase consent and status, Settings
(Appearance, Voice, Suggestions, Capabilities, Account and security), image attachments, focus modes, move/rename.

## Behaviour changes that required updating existing tests

Reported because the tests encoded 0.2 behaviour that the 0.3 brief asked to change:

- Backend `test_workflows.py`: three tests now expect the new first question (the restated idea, topic `intent`) before
  the template questions, and the image-recognition template asks about users before the subject scope. One assertion
  follows the reworded offline note ("No AI model is available: …").
- Frontend `machine.test.ts`: rewritten for the v2 states (`SEND` → *understanding*, `ACCEPTED` → *thinking*, an aborted
  reply → *cancelled*, *awaiting your answer* instead of *waiting for input*).
- Frontend `voice.test.tsx`: replies are spoken only for voice requests, a stopped reply ends in *cancelled*, and the
  unsupported-browser message changed. `command.test.tsx`: the example now goes through idea detection instead of the
  removed Ideas panel; a waiting question opens in place. `app.test.tsx`: the model error is now part of a sentence.

## What was deliberately not changed

Authentication, sessions, CSRF, rate limiting, workspace confinement, change proposals, the command allowlist, approval
binding and audit logging work as in 0.2. Voice cannot approve anything. No unrestricted shell, no autonomous
consequential action, no background monitoring of external systems, and no fabricated research, progress or results.

# 0.2: holographic, voice-first, proactive

## Baseline (observed before any change)

- Source: the released `NOVA-Personal-AI-Assistant.zip` (0.1.0). The working copy was compared file by file with the
  extracted archive (identical), committed as `main`, and all work happened on the branch
  `feature/holographic-voice-workflows`.
- Baseline tests: backend **94 passed**; frontend **19 passed**; TypeScript type check passed.
- Documentation read: ARCHITECTURE, SECURITY (permission model and threat model), DATA_MODEL, TESTING.

## Integration points

| Need | Existing piece reused | How |
|---|---|---|
| Conversation for voice | `store/chat.ts` → `POST /api/conversations/{id}/messages` (SSE) | The chat store now emits stream events (`send`, `delta`, `error`, `aborted`, `end`). Voice and the state machine subscribe to them; there is still exactly one conversation engine. |
| Interruption | `AbortController` in the chat store + server-side cancellation (partial reply stored as `cancelled`) | `voice.interrupt()` cancels speech output and aborts the stream; "Continue the reply" sends a normal follow-up message in the same conversation. |
| AI providers | `providers/` registry + `services/assistant.py` (`resolve_provider`, `build_system_prompt`, `untrusted`) | Workflows call the same provider interface; `pick_ai` adds a live status check so "auto" never pretends a model answered. |
| Research | `research/` (`SearxngResearch`, `DisabledResearch`) | Workflow research stores real results with URLs or records questions as `pending`. |
| Documents | `artifacts` table and Design tab editor | Every workflow document is an ordinary editable artifact; deep links `?artifact=ID`. |
| Approvals | `permissions/` (`ActionRequest`, parameter hash, single use, expiry) + `api/actions.py` | Command tasks create a normal `ActionRequest`; hooks update the task on approve, reject, expiry and on the observed command result. |
| Command execution | `tools/commands.py` allowlist runner + `tasks/` threads | Unchanged; `on_task_finished` maps the real exit status to the task. |
| Audit | `services/audit.py` | New audit events: `workflow.*`, `work.*`. |
| Recovery | startup recovery in `main.py` | Extended to workflow stages (`interrupted`) and running work items. |

## What changed

**Backend** (`backend/app/workflows/`, `services/work.py`, `services/suggestions.py`, `api/workflows.py`, `api/work.py`,
migration `0002`)
- Persistent idea-to-product workflows: capture, clarify (one question at a time), research, analysis, architecture,
  UX, technology, documents, proposal, decision, tasks. Six reusable templates (general, software product,
  image-recognition app, marketing campaign, research study, business idea).
- Questions: the AI picks the most consequential open topic (validated JSON); without AI, the template's next topic.
- Documents: AI drafts when a model answers a live check; otherwise drafts assembled only from the owner's answers,
  the template's guidance and explicit TODOs. Documents the owner edited are never overwritten.
- Proposal approval bound to the SHA-256 of the reviewed proposal; revise (reopen from any stage) and reject.
- Task engine (`work_items`, `work_events`): dependencies with cycle checks, manual transitions, command tasks through
  the approval system, document tasks through the AI provider, observed results, cancellation, recovery.
- Next-step suggestions computed from real state on request.

**Frontend**
- Command center (`/`): three.js core (lazy-loaded, WebGL; SVG fallback), floating panels with real data, push-to-talk,
  live transcript, waveform from the real microphone (or word-boundary bars while speaking), interrupt (button or Esc),
  continue after interruption, text input, model and microphone status.
- Assistant state machine (`assistant/machine.ts`) with an explicit transition table; ambient states derived from
  observed backend state (`assistant/wiring.ts`).
- Voice adapters (`voice/`): `SpeechInputAdapter` / `SpeechOutputAdapter` interfaces with browser implementations;
  a streaming speaker that speaks sentences as they arrive and skips code.
- New screens: Ideas (`/workflows`), workflow detail (`/workflows/:id`), project Work board. Compact navigation rail.
  The full chat moved to `/chat` (unchanged features) and now uses the shared voice controller.

## What was deliberately not changed

Authentication, sessions, CSRF, rate limiting, file confinement, change proposals, the command allowlist, approval
binding and audit logging work exactly as in 0.1; their tests pass unchanged. No unrestricted shell, no background
listening, no autonomous consequential action, and no fabricated research, progress or results were added.
