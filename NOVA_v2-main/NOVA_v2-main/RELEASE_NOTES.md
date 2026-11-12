# NOVA 0.4.1 — actions that actually happen

NOVA now carries out requests to act instead of explaining them. Diagnosis, design, setup, the live test record and
the limitations are in [docs/ACTIONS.md](docs/ACTIONS.md); the full list of changes is in CHANGELOG.md. Built and
verified on 2026-10-18 on the owner's Windows 11 PC (Python 3.13, Ollama 0.40.5 with llama3.2, Spotify connected).

| Check | Result |
|---|---|
| Baseline before changes | Backend 167 passed, 3 failed, 1 skipped. Frontend 73 passed, 1 failed. The failures are wake-word tests that also fail after the changes. |
| Backend tests (279) | 275 passed, 1 skipped, 3 failed. The failures are the same pre-existing wake-word tests in `test_voice.py`: `test_default_wake_detector_accepts_both_nova_phrases` and two false-wake recordings. |
| Backend lint (`ruff check app tests`) | passed |
| Frontend tests (78) | 77 passed, 1 failed (the same pre-existing wake-word re-arm timeout in `voiceLocal.test.tsx`) |
| TypeScript check and production build | passed |
| Migration `0004` on a copy of the owner's real database | 0003 → 0004; all rows kept; `alembic check`: no differences |
| Live, model-driven Spotify action | The exact request went through NOVA's chat API, local llama3.2 and the owner's real Spotify account. NOVA asked which track (no Tiakola track is named “Vie de Pirate”). After “the first one”, Spotify reported “Cœur De Pirate” by Tiakola playing on LAPTOP-EXPERTIZ, **verified**. |
| Live pipeline probe (OS and Spotify simulated, real llama3.2) | play → verified; pause → verified; open the calculator → opened; two questions → no tools; “turn on the kitchen lights” → nothing executed |
| Browser check of the new UI (Chromium) | Action cards, Settings → Actions, emergency stop and resume work against a real backend |

Not verified: the spoken (microphone) path to actions on this PC, Home Assistant with a real hub, Claude and mistral
tool calling against live services, and Spotify Premium-only behaviour.

# NOVA 0.3.0 — release notes and verification report

0.3 evolves 0.2.0 into a calmer, theme-aware, voice-first workspace with a local wake phrase, ideas that start from a
sentence, image understanding and an honest capability report. It was developed on the branch
`feature/v0.3-voice-themes-capabilities` of a working copy restored from the verified 0.2.0 archive. Built and verified
on 2026-10-17 (Linux x86-64, 2 vCPU, Python 3.13, Node 22, npm 10, no GPU, no live AI provider, no physical microphone).
See docs/EVOLUTION.md and docs/VOICE.md.

## Baseline before changes

| Check | Result |
|---|---|
| Backend tests (0.2.0) | 122 passed |
| Frontend tests (0.2.0) | 40 passed |
| TypeScript type check | passed |

## Verification of 0.3.0

Every result below was observed.

| Check | Command / procedure | Observed result | Status |
|---|---|---|---|
| Backend tests | `python -m pytest` | 163 passed (122 existing — 3 updated for the new first question, see below — + 41 new), exit 0; four consecutive full runs green after the runner fix | Pass |
| Lint | `ruff check app tests` (+ `scripts/`) | All checks passed | Pass |
| Migrations | `python -m app.db.migrate` on an empty folder + `alembic check` | upgraded to 0003; "No new upgrade operations detected" | Pass |
| Frontend tests | `npm test` | 10 files, 71 tests passed (40 before; 12 existing tests updated or replaced for the requested behaviour changes, listed below) | Pass |
| Type check / build | `npm run typecheck`, `npm run build` | exit 0; main bundle 717 kB (217 kB gzip); three.js core lazy chunk 543 kB (136 kB gzip); CSS 50 kB | Pass |
| Contrast | `prefs.test.ts` parses the tokens in `styles.css` | every text colour ≥ 4.5:1 on every surface, in both themes; button inks ≥ 4.5:1 | Pass |
| Accessibility audit | axe-core 4.10 (WCAG 2.1 A/AA rules), 12 screens × light and dark, real backend | 0 violations (after fixing two: badge contrast in the light theme and an unlabelled voice selector) | Pass |
| Wake phrase + local STT (backend) | `tests/test_voice.py` on speech recordings | “hey nova” detected in both positive recordings, none in three negative recordings; transcripts produced; WebSocket end to end; every refusal path | Pass |
| Wake phrase in a real browser | `scripts/dev/browser_e2e.py`: Chromium, fake microphone playing “Hey Nova. I want to build an app that helps people check whether their plants are healthy using their phone camera.” | armed 0.8 s after consent; wake heard; transcribed locally; opened as an idea that restates the request; then asked about users, then which plants; microphone start audited without content; microphone off from the sidebar | Pass |
| Browser checks (same run) | themes, reduced motion, persistence, image attachment, mobile, blocked microphone | system theme follows OS dark/light; explicit dark overrides OS light and survives reload; reduced motion applied; image uploaded and previewed under the CSP; message with image sent and the model's real connection error shown; 390 px wide with no horizontal scroll; denied microphone explained with *Try again*; 0 console errors — 22/22 steps | Pass |
| E2E smoke (no mocks) | `python scripts/smoke_test.py` | 20 PASS, 1 UNVERIFIED (live AI), exit 0 | Pass |
| Plant-health demo | `python scripts/demo_plant_health.py --temp --approve` | template chosen automatically (image recognition); intent → users → plants → … 14 questions one at a time; all stages done offline; 5 research questions pending; 12 editable documents; approval created 7 tasks | Pass |
| Upgrade from 0.2 | 0.2.0 backend creates a chat, a project and a workflow with one answer; 0.3.0 starts on the same data folder | migrated to 0003; chat (mode *general*), project and workflow intact; the 0.2 workflow continues with its next question (no intent question inserted) | Pass |
| Archive | `package_release.py --verify-extracted` | 253 entries, CRC OK, essential files present, no secrets, databases, `.env`, `node_modules`, virtualenvs or build-machine paths; extracted copy: 163 backend tests passed, smoke test exit 0 | Pass |
| Clean install from the ZIP | Extract → `bash scripts/setup.sh` (fresh venv, pip with the PocketSphinx wheel, `.env`, migrations, `npm ci`, build) → tests → `bash scripts/start.sh` | setup exit 0 in 29 s; 163 backend + 71 frontend tests and the type check passed in the clean copy; health, UI and the audio worklet served (HTTP 200); voice WebSocket fed a real recording: `wake → listening → speech → transcribing → final "what is the weather like today"`; audit recorded only stream start and end | Pass |
| PocketSphinx versions | `test_voice.py` and the measurements with 5.1.1 and 5.0.4 | identical results; 5.0.4 pinned because it has wheels for Python 3.11–3.13 on Windows, macOS and Linux (5.1.1 has none for 3.12) | Pass |

### Acceptance criteria

| Criterion | Evidence | Status |
|---|---|---|
| Minimal, premium interface; panels only when relevant | Redesigned command center; screenshots; side panels render only with content | Verified (design review by screenshots) |
| Coherent light theme, dark theme, system option, persisted | Token sets per theme, contrast test, axe audit, browser E2E | Verified |
| Reduced motion persisted and applied | Settings → Appearance; core stops animating; browser E2E | Verified |
| Core reflects real state | State machine v2 driven by real events; browser E2E observed Listening → Transcribing → Understanding → Waiting for your answer | Verified |
| Genuine local wake phrase (not browser recognition) | PocketSphinx KWS in the backend; recordings and real Chromium | Verified with synthetic speech |
| Push-to-talk fallback, STT/TTS adapters, transcripts, barge-in, standby return | `voice.test.tsx`, `voiceLocal.test.tsx`, `machine.test.ts` | Verified with fakes (TTS) and synthetic speech (STT) |
| No continuous raw audio to a remote service for the wake phrase; consent | Local detection only; consent required by the API; disclosure in the notice | Verified |
| Recoverable microphone permission errors | Browser E2E with denied permission; unit tests | Verified |
| Idea → summary → one question at a time → … → approval → tasks; flexible | Command center focus, skip/continue/explore, “Not an idea?”, demo and smoke | Verified |
| Plant example: users and plants asked; visible symptoms ≠ full health | Question order verified; template guidance requires “visible features only … never definitive” in every document (`test_workflows.py`) | Verified (offline drafts); AI drafts unverified (no model) |
| Modular capabilities with honest reporting | `GET /api/capabilities`, Settings → Capabilities (11 domains) | Verified |
| Security preserved | All 0.2 security tests pass unchanged; voice WebSocket refusals tested; voice cannot approve | Verified |
| Works without a local model | Every check above ran with no AI provider | Verified |

## Existing tests that changed (requested behaviour changes)

- Backend `test_workflows.py`: 3 tests now expect the restated idea (topic `intent`) as the first question and users
  before scope; 1 assertion follows the reworded offline note.
- Frontend: `machine.test.ts` (7 of its 8 tests updated for the v2 states), `voice.test.tsx` (2 tests and the shared
  setup: spoken replies only for voice requests, a stopped reply is *cancelled*, new unsupported-browser text),
  `command.test.tsx` (2: idea detection replaces the removed Ideas panel; a waiting question opens in place),
  `app.test.tsx` (1: the model error is part of a sentence).

## Bugs found and fixed during verification

- **Command start-up race (since 0.1)**: a command that was still starting when NOVA shut down, or when its task was
  cancelled, kept running. The existing test `test_shutdown_stops_running_commands` failed intermittently (2 of about 8
  full runs). The runner now refuses to start after shutdown and applies a cancellation that arrives while the process is
  starting; a new regression test covers both.
- A slow preference load could overwrite a theme change made meanwhile; fixed and tested.
- Image previews were blocked by the CSP (`img-src` now allows `blob:`).
- Existing 0.2 workflows would have been interrupted by the new intent question; it is now asked only for new ideas.

## Not verified (needs your environment)

- A live Ollama or Anthropic response, AI-written idea summaries and documents, and image understanding by a vision model.
- A human voice through a physical microphone, real rooms and accents; speech synthesis through speakers.
- Browser speech recognition (needs Chrome/Edge with network access to the vendor's service).
- Vosk with a real model (model downloads were blocked; tested with a fake module).
- Firefox and Safari (only Chromium was run); Windows PowerShell scripts (parsed only previously; bash scripts ran).
- Live SearXNG research.

## Known limitations

- Local wake detection and PocketSphinx transcription are US-English; local transcription makes word errors (“plants”
  heard as “plans”), which is why NOVA restates every idea for confirmation.
- One-word wake phrases cause more false alarms (measured in docs/VOICE.md); NOVA warns but allows them.
- Without an AI model, idea summaries are faithful restatements and documents are scaffolds of your answers.
- While NOVA speaks, the waveform shows word boundaries, not audio (browsers do not expose synthesized audio).

## Required configuration

Nothing to start. Optional: Ollama (`ollama pull llama3.2`, or a vision model such as `llama3.2-vision`) or
`NOVA_ANTHROPIC_API_KEY`; SearXNG for research; a microphone for voice; Vosk + a model for better offline dictation.
The database migrates automatically to 0003 on first start. No speech model download is needed.
