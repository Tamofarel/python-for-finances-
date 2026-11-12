# NOVA — personal AI assistant

NOVA is a local-first personal AI workstation inspired by JARVIS: a voice-first holographic command center
where you talk ideas through, turn them into validated project proposals, design and build software, and run
approved commands — with you deciding every consequential step.

**New in 0.3:** a calmer, minimal command center with light, dark and system themes; a **local wake phrase**
(“hey nova”, detected on your computer) with offline transcription, push-to-talk, spoken replies, barge-in and
clear microphone indicators; ideas that start from a sentence (“I want to build…”), are restated for you to confirm and
are clarified one question at a time; image and screenshot attachments; conversation focus modes; and an honest
capability report. See [docs/EVOLUTION.md](docs/EVOLUTION.md) and [docs/VOICE.md](docs/VOICE.md).

It is a real application (FastAPI + React), not a mockup: conversations persist in SQLite, edits become
reviewable diffs, commands run only after you approve the exact command, and every important action is
audited. When an AI provider is unavailable, NOVA says so; it never fakes answers, search results or
test results.

```
Idea → Discussion → Analysis → Research → Design → Validation → Implementation → Testing → Review → Delivery
```

## Contents

- [What works](#what-works)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Windows setup](#windows-setup)
- [Linux / macOS setup](#linux--macos-setup)
- [Configuration](#configuration)
- [First run](#first-run)
- [Running NOVA](#running-nova)
- [Tests](#tests)
- [AI providers: Ollama and Anthropic](#ai-providers)
- [Appearance](#appearance)
- [Voice](#voice)
- [Ideas: from idea to validated project](#ideas-from-idea-to-validated-project)
- [Tasks and next steps](#tasks-and-next-steps)
- [Research](#research)
- [Workspaces and permissions](#workspaces-and-permissions)
- [Tool execution security](#tool-execution-security)
- [Troubleshooting](#troubleshooting)
- [Limitations](#current-limitations)
- [Roadmap](#roadmap)

## What works

| Area | What you can do |
|---|---|
| Conversations | Create, rename, search (titles and message text), delete with confirmation; streaming replies (SSE); stop a generation; retry; copy; safe Markdown with syntax highlighting and Mermaid diagrams; history survives refresh and backend restarts. |
| Models | Ollama (local) and Anthropic (cloud) behind one provider interface. Live status distinguishes *configured*, *reachable*, *models reported* and the *actual error*. Pick provider and model in the UI. |
| Accounts & security | First-run owner setup protected by a one-time code printed in the terminal; Argon2id passwords; server-side sessions (HttpOnly, SameSite=Strict cookies); CSRF tokens; login rate limiting; strict CSP and security headers; audit log. |
| Projects | Create, describe, give NOVA project instructions, archive, delete (by typing the name), attach conversations, browse activity. Each project has a confined workspace folder. |
| Files | Browse, read, create files/folders; edit text files; every edit becomes a proposal with a unified diff you apply or discard; revert applied changes; move or rename after your approval (never overwriting); secrets (`.env`, keys…) hidden; path traversal and symlink escapes blocked. |
| AI-assisted development | "Ask NOVA to change it" produces a diff for the file *you* chose; "Summarize project" inspects the tree and key files (read-only) and saves a summary. |
| Idea analysis | Problem, users, assumptions, feasibility, risks, alternatives, recommendation, architecture, milestones and open questions — saved as an editable artifact. |
| Design workspace | Requirements, user stories, architecture, API contract, data model, UI specs, component inventory, implementation plan, review summary. Edit, copy, export to the workspace as a reviewable change. |
| Commands | Request an allowlisted command (tests, builds, git status…), review it, approve once; see real stdout/stderr, exit code and duration; cancel while running. |
| Approvals | One place for pending consequential actions, with what/why/affected/reversible/permission and the observed result. |
| Memory | Only what you explicitly save: preferences, facts and notes, general or per project; search, edit, disable, delete, clear. |
| Command center | The home screen: a three.js holographic core (SVG fallback without WebGL) that shows NOVA's real state, the exchange as captions, one input for text, voice and images, up to three next-step suggestions, and side panels that appear only when relevant (approvals waiting, tasks in motion, the idea's documents, a long reply, the project). |
| Appearance | Light (silver and white), dark (deep blue-grey) and system themes; reduced motion (system, reduce, full); saved per owner and remembered by the browser; WCAG AA contrast checked by a test. |
| Voice | Local wake phrase (PocketSphinx on your computer, configurable phrase and sensitivity, consent first), push-to-talk, transcription by the browser or locally (PocketSphinx, optional Vosk), live transcript and waveform, spoken replies streamed sentence by sentence, barge-in, follow-up listening, microphone status and one-click off, recoverable permission errors. See [docs/VOICE.md](docs/VOICE.md). |
| Images | Attach, paste or drop up to four images (PNG, JPEG, GIF, WebP, ≤ 5 MB) to a message; sent to vision-capable models (Claude, Ollama vision models); refused with an explanation when the selected model cannot read images. Stored per owner, deleted with the conversation. |
| Focus modes | Per conversation: general, software engineering, research, product, design, business, learning — a system-prompt preset, nothing hidden. |
| Capabilities | Settings → Capabilities lists every domain (conversation, engineering, research, product discovery, UI/UX, vision, business, learning, files, automation, voice) with its status from live checks and why. |
| Ideas (workflows) | Say or type “I want to build…” on the command center → NOVA restates the idea for you to confirm or correct → one focused question at a time → research (real or explicitly pending) → feasibility, architecture, UX, technology → editable documents (PRD, user stories, data model, API contract, implementation plan, risk register) → consolidated proposal → approve / revise / reject → implementation tasks. Persists across refreshes and restarts. |
| Task engine | Tasks with domain, dependencies, inputs/outputs, events and errors; manual tasks, command tasks that go through approvals, and document tasks drafted by the AI. Status follows real outcomes. Next-step suggestions on request. |
| Research | SearXNG-backed web search with sources and an optional brief that separates facts, inferences, unverified claims and conflicts. Off unless configured. |
| Actions (0.4.1) | Ask, typed or spoken, and NOVA acts: “open Spotify and play …”, “pause the music”, “open the calculator”, “open github”, smart-home devices through Home Assistant. The model picks a registered tool, NOVA validates and runs it with a deadline, verifies the result where possible, and shows what really happened. Safety-critical actions wait for on-screen approval. There is an emergency stop. See [docs/ACTIONS.md](docs/ACTIONS.md). |
| Isolated browser (optional) | With Playwright and Chromium installed, NOVA can open approved public HTTPS sites in a visible, isolated browser, inspect visible text, search the page and follow checked links. Forms and downloads are not automated. See [docs/ACTIONS.md](docs/ACTIONS.md). |

## Architecture

A modular monolith designed to be read and extended by one developer:

```
frontend/  React 18 + TypeScript (strict) + Vite, zustand stores, typed API client
backend/   FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite, HTTPX, official Anthropic SDK
  app/api/          HTTP routes (thin)            app/services/   domain logic (auth, changes, prompts, audit)
  app/providers/    AI provider interface + Ollama + Anthropic + registry
  app/workspaces/   confined file access           app/tools/      allowlisted command runner
  app/permissions/  approval model (A/B/C)         app/tasks/      background command tasks
  app/research/     search provider interface      app/models/     ORM models
  app/workflows/    idea-to-product engine, templates, drafts, background stage runner
  app/voice/        wake detector, local transcribers, voice session (endpointing)
  migrations/       Alembic migrations (0001–0003) tests/          pytest suite (+ audio fixtures)
frontend/src/
  assistant/        state machine + wiring to real events      hologram/   three.js core, SVG fallback, waveform
  voice/            browser speech adapters, microphone capture (AudioWorklet), local voice stream, speaker
  store/prefs.ts    theme, motion, voice and suggestion preferences (server-stored)
  pages/command/    command center and panels                  pages/workflow/  ideas and workflow screens
docs/      architecture, API, security & threat model, data model, testing, development
scripts/   setup / start / dev / test (PowerShell + bash), smoke_test.py, package_release.py
```

In production one process (`python -m app.main`) serves the API and the built frontend on
`http://127.0.0.1:8000`. In development Vite serves the UI on `:5173` and proxies `/api` to the backend.
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prerequisites

- **Python 3.11 or newer** (tested with 3.13)
- **Node.js 20 LTS or newer** with npm (tested with Node 22 / npm 10) — needed to build the UI
- About **540 MB** of disk for dependencies (`backend/.venv` ≈ 180 MB including the 38 MB PocketSphinx speech package,
  `frontend/node_modules` ≈ 355 MB, built UI ≈ 4 MB). No speech model download is needed.
- A microphone only if you want voice (Chrome or Edge for every voice feature; see [docs/VOICE.md](docs/VOICE.md))
- No GPU, no Docker, no API key required to open and use the local interface
- Optional: [Ollama](https://ollama.com) for local models (each model needs its own disk space —
  `llama3.2` (3B) is about 2 GB and wants ~4 GB of free RAM), or an Anthropic API key

## Windows setup

From PowerShell in the `NOVA` folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

`setup.ps1` creates `backend\.venv`, installs pinned dependencies, copies `.env.example` to `.env`,
runs the database migrations and builds the frontend. It downloads no AI model.

Manual equivalent:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
Copy-Item .env.example .env
cd backend; .\.venv\Scripts\python.exe -m app.db.migrate; cd ..
cd frontend; npm ci; npm run build; cd ..
cd backend; .\.venv\Scripts\python.exe -m app.main
```

## Linux / macOS setup

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

## Configuration

All settings are environment variables prefixed `NOVA_`, read from `.env` in the NOVA folder (or
`backend/.env`). See [.env.example](.env.example) — it contains placeholders only. Highlights:

| Variable | Default | Purpose |
|---|---|---|
| `NOVA_HOST` / `NOVA_PORT` | `127.0.0.1` / `8000` | Network binding. Keep `127.0.0.1` unless you put HTTPS in front. |
| `NOVA_DATA_DIR` | `./data` | Database (`nova.db`) and default workspaces. |
| `NOVA_ALLOWED_WORKSPACE_ROOTS` | *(empty)* | Extra folders projects may use, `;`-separated. |
| `NOVA_DEFAULT_PROVIDER` | `ollama` | Provider used until you choose one in the UI. |
| `NOVA_OLLAMA_BASE_URL`, `NOVA_OLLAMA_MODEL` | `http://127.0.0.1:11434`, `llama3.2` | Local models. |
| `NOVA_ANTHROPIC_API_KEY`, `NOVA_ANTHROPIC_MODEL` | *(empty)*, `claude-sonnet-5-5` | Cloud models. |
| `NOVA_RESEARCH_PROVIDER`, `NOVA_SEARXNG_URL` | `none` | Web research. |
| `NOVA_COMMAND_ALLOWLIST` | tests/builds/git read commands | Allowed command prefixes. |
| `NOVA_DESKTOP_CONTROL_ENABLED` | `false` | Lets NOVA act on this PC when you ask: Spotify, approved apps, allowed websites, project files, media keys. Never arbitrary shell commands. See [docs/ACTIONS.md](docs/ACTIONS.md). |
| `NOVA_SPOTIFY_CLIENT_ID` | *(empty)* | Spotify app ID used for Authorization Code with PKCE. No Spotify password or client secret is stored. |
| `NOVA_DESKTOP_APPS`, `NOVA_BROWSER_ALLOWED_HOSTS` | *(empty)*, common sites + localhost | Extra apps NOVA may open; approved hosts. The isolated browser accepts only public HTTPS destinations; localhost remains available only to the default-browser opener. |
| `NOVA_HOME_ASSISTANT_URL`, `_TOKEN`, `_ENTITIES` | *(empty)* | Optional smart-home control through your Home Assistant hub, limited to the listed devices. |
| `NOVA_COOKIE_SECURE` | `false` | Set `true` when served over HTTPS. |
| `NOVA_VOICE_ENABLED`, `NOVA_VOICE_ALLOW_REMOTE` | `true`, `false` | Local voice stream; microphone audio only from this computer unless allowed. |
| `NOVA_VOSK_MODEL_PATH` | *(empty)* | Optional Vosk model folder for better offline transcription. |
| `NOVA_MAX_IMAGE_BYTES` | `5000000` | Largest image attachment. |

### Database and migrations

The schema is managed by Alembic. Migrations run automatically at startup and can be run manually:

```bash
cd backend && python -m app.db.migrate
```

After changing models: `cd backend && alembic revision --autogenerate -m "describe change"` (with
`NOVA_DATA_DIR` pointing at a scratch folder), review the file in `migrations/versions/`, commit it.

## First run

1. Start NOVA. The terminal prints a box with **NOVA first-run setup code: 12345678**.
2. Open `http://127.0.0.1:8000`, enter the code, choose a username and a password (10+ characters).
3. You are signed in. There is no default account and no known password. The code changes every time
   the backend starts and stops working once the owner exists.

## Running NOVA

| Mode | Command | URL |
|---|---|---|
| Production (one process) | `scripts\start.ps1` or `cd backend && python -m app.main` | http://127.0.0.1:8000 (command center), `/chat` for the full conversation |
| Development (auto-reload) | `scripts\dev.ps1` (two windows) | http://127.0.0.1:5173 |
| Health | `GET /api/health`, readiness `GET /api/ready` | |

**Stop** with `Ctrl+C`. Shutdown stops any running command and closes the database cleanly; anything
that was interrupted is marked as such on the next start.

## Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

or individually:

```bash
cd backend && python -m pytest && python -m ruff check app tests
cd frontend && npm test && npm run typecheck && npm run build
python scripts/smoke_test.py      # starts a real backend in a temp folder and walks the main workflow
python scripts/dev/browser_e2e.py # optional: real Chromium + fake microphone (needs Playwright), see the file header
```

AI providers are mocked in unit tests (clearly labelled); those tests do not prove a live provider
works. The smoke test reports live AI steps as **UNVERIFIED** when no provider is reachable. See
[docs/TESTING.md](docs/TESTING.md).

## AI providers

### Ollama (local, private)

1. Install Ollama from https://ollama.com and start it.
2. Pull a model yourself (NOVA never does): `ollama pull llama3.2` (≈2 GB). Smaller: `qwen2.5:1.5b` (≈1 GB).
3. In `.env`: `NOVA_OLLAMA_BASE_URL=http://127.0.0.1:11434` and `NOVA_OLLAMA_MODEL=llama3.2`.
4. Open **Models** in NOVA and press **Check again**. Ollama shows *Connected* only when it actually
   answers; a missing model is reported with the exact `ollama pull` command.

Without a GPU expect a few tokens per second on a 3B model; long analyses can take a minute.

### Anthropic (cloud)

1. Create an API key in the Anthropic Console.
2. In `.env`: `NOVA_ANTHROPIC_API_KEY=…` (and optionally `NOVA_ANTHROPIC_MODEL`). Restart the backend.
3. In **Models**, select *Anthropic Claude (cloud)*; the model list comes from the API.

The key is read only by the backend: it is never sent to the browser, written to the database, logged,
or passed to commands NOVA runs. Messages you send to a cloud model leave your computer.

## Appearance

Settings → Appearance: *Match my system*, *Light* or *Dark*, and motion (*Match my system*, *Reduce motion*, *Full
motion*). The sun/moon/monitor button in the sidebar cycles the theme. With reduced motion the core changes colour with
NOVA's state but does not animate. Preferences are saved to your account and cached by the browser.

## Voice

Everything voice-related is optional, and text always works. Details, measurements and privacy notes:
[docs/VOICE.md](docs/VOICE.md).

Narrow Windows Spotify/media controls are documented in [docs/NOVA_0.4_SETUP.md](docs/NOVA_0.4_SETUP.md).
The incremental implementation and verification boundaries are in [docs/NOVA_0.4_REPORT.md](docs/NOVA_0.4_REPORT.md).

- **Wake phrase**: click the microphone at the bottom of the sidebar, read the notice and choose how speech is
  transcribed after the phrase (the browser — most accurate, but Chrome/Edge send that audio to Google/Microsoft — or
  locally with PocketSphinx). The wake phrase itself is always detected **on your computer** by NOVA's backend. Change
  the phrase (e.g. “hey nova”, “ok computer”), sensitivity and chime in Settings → Voice. Click the microphone again to
  turn it off.
- **Talk**: hold the button and speak, release to send; a click (or Enter/Space) toggles listening instead.
- **Spoken replies**: answers to voice requests are read aloud sentence by sentence while they stream (code is
  summarised as “I've put the code on screen”). Typed messages are not read aloud; use *Read aloud* on any reply.
- **Interrupt**: the wake phrase, *Talk*, *Stop* or Esc stop speech and the reply being generated immediately.
- **Ideas by voice**: “Hey NOVA, I want to build an app that…” starts an idea; NOVA restates it, asks one question at a
  time and reads each question aloud. Say “skip”, “continue”, “stop” or “yes”. Approvals are never given by voice.

## Ideas: from idea to validated project

On the command center, say or type the idea (“I want to build…”, “I have an idea for…”, “Je veux créer…”). NOVA
recognises idea-shaped sentences, picks the closest kind (general, software product, image-recognition app, marketing
campaign, research study or business idea — shown with the reason), and opens it in place. If it was not an idea,
*Not an idea? Send it as a normal message* sends it to the conversation instead. You can also start from **Ideas** and
choose the kind yourself.

1. NOVA first **restates the idea** (“Here is how I understood your idea: … Did I get that right?”) so you can confirm or
   correct it — with a model the summary is the AI's, without one it is a faithful rephrasing of your words.
2. NOVA asks **one question at a time** — the most consequential open decision (chosen by the AI when available,
   otherwise the next item of the template's checklist). Answer, skip, or *Enough questions, explore it*.
3. **Explore everything** runs the remaining stages one after another (or go step by step on the workflow screen; any
   stage except the proposal can be skipped).
4. **Research** runs real searches when SearXNG is configured and stores the URLs; otherwise every research question
   is marked **pending** with where to look. Nothing is invented.
5. **Analysis, architecture, UX, technology and documents** produce editable artifacts. With a reachable AI model they
   are AI drafts that must label statements as Verified (with a source URL), Assumption or Recommendation. Without
   one, NOVA assembles drafts from your own answers and the template's guidance, with explicit TODOs.
6. The **proposal** summarises decisions, research status, documents and missing decisions. *Approve* is bound to the
   exact version you reviewed; *Ask for a revision* reopens any stage; *Reject* closes it.
7. Approval turns the implementation plan into **tasks** on the project's Work board.

You can leave at any point; the workflow, its answers and documents persist across refreshes and restarts.

### Example: plant-health photo app

Say “Hey NOVA, I want to build an app that helps people determine whether their plants are healthy using their
smartphone camera” (or click *a plant-health photo app* under the core and press Send). NOVA picks the image-recognition
template because the idea mentions a camera, restates the idea, then asks about the intended users, which plants and use
case, visible symptoms and possible causes (including “no visible symptoms”), dataset quality, pretrained models,
on-device vs server inference, phones and photo guidance, uncertainty handling, offline use and running cost, privacy,
backend needs, validation and success metrics. Exploration covers research (real or pending), feasibility, architecture,
the capture → quality check → result → next-steps experience, technology choices, an MVP roadmap and risks. Every
document must treat a result as a *visible-symptom estimate with confidence* — a photo shows visible symptoms but cannot
establish every aspect of a plant's health — never as a confirmed diagnosis.

To run it unattended through the API with example answers (`docs/examples/plant-health-answers.json`):

```bash
python scripts/demo_plant_health.py --temp            # throw-away backend and data folder
python scripts/demo_plant_health.py --user farel      # against your running NOVA
```

## Tasks and next steps

Each project has a **Work** board. Tasks can be done by you (you update the status), by an allowlisted command (NOVA
requests your approval, runs it, and marks the task done or failed from the real exit code), or by the AI (a document
draft). Dependencies block starting until they are done. The command center suggests up to three **next steps** — an
approval waiting, an idea's open question (answered in place), a proposal, a ready or failed task — computed from real
state while the command center is open. NOVA does not monitor anything in the background and never acts on a suggestion
by itself; turn suggestions off in Settings → Suggestions.

## Research

Research is off by default. To enable it, run a [SearXNG](https://docs.searxng.org) instance you control
with the JSON format enabled (`search.formats: [html, json]` in its settings), then:

```
NOVA_RESEARCH_PROVIDER=searxng
NOVA_SEARXNG_URL=http://127.0.0.1:8888
```

Project → **Ideas** → *Research* shows the results with their URLs and can write a brief that keeps
source-backed facts separate from inferences. Only search snippets are used (NOVA does not fetch pages
yet). If search fails, NOVA tells you; it never invents sources.

## Workspaces and permissions

Each project has one workspace folder. By default NOVA creates it under `data/workspaces/`. To work on
an existing folder (for example `C:\Users\you\Projects`), add its parent to
`NOVA_ALLOWED_WORKSPACE_ROOTS` and enter the path when creating the project.

Inside a workspace NOVA can read text files (≤1 MB) and create new ones. Existing files change only
through an approved proposal. `.env`, keys, certificates, `.git`, `.ssh` and similar are blocked.

| Category | Examples | Rule |
|---|---|---|
| A — read-only | read files, list folders, search history | automatic, within the workspace |
| B — reversible | edit or create files, export documents, move or rename | proposal + diff (or a move request), applied only on approval bound to the exact parameters, revertible |
| C — consequential | run commands, delete files | explicit confirmation of the exact parameters, once, within 10 minutes |

These rules are enforced by the backend. See [docs/SECURITY.md](docs/SECURITY.md).

## Tool execution security

- Commands are argument lists, never shell strings; `;`, `|`, `&`, `$`, backticks, `..` and absolute
  paths are rejected.
- The command must start with an allowlisted prefix (`NOVA_COMMAND_ALLOWLIST`), and it runs in the
  project workspace with a minimal environment (no API keys or NOVA settings), a timeout and an output cap.
- Approval is tied to a hash of the exact parameters; a replayed or altered approval is refused.
- NOVA never runs a command because a model suggested it. Model output is treated as untrusted data.

`npm test` and similar can run arbitrary code defined in the project's own `package.json` — only approve
commands for projects you trust.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot connect to Ollama" | Start Ollama (`ollama serve`) and check `NOVA_OLLAMA_BASE_URL`. |
| "Model 'llama3.2' is not installed" | `ollama pull llama3.2`, or choose an installed model in **Models**. |
| "Anthropic rejected the API key" | Check `NOVA_ANTHROPIC_API_KEY` in `.env`, restart the backend. |
| Lost the setup code | Restart the backend; a new code is printed while no account exists. |
| Forgot the password | Stop NOVA, delete `data/nova.db` (this erases NOVA's data; workspace files are kept), start again. |
| UI says it can't reach the backend | Is `python -m app.main` running? In dev mode both servers must run. |
| "Executable 'npm' was not found" | Node.js must be on the PATH of the terminal that started NOVA. |
| `scripts\*.ps1` blocked | Use `powershell -ExecutionPolicy Bypass -File …`. |
| Port 8000 in use | Set `NOVA_PORT=8010` in `.env`. |
| Microphone blocked / wake phrase not heard | See the troubleshooting table in [docs/VOICE.md](docs/VOICE.md#troubleshooting). |
| “The selected model does not read images” | Choose a vision model in **Models** (e.g. `llama3.2-vision` in Ollama, or Claude). |
| The theme flickers or looks wrong | Settings → Appearance; *Match my system* follows the OS setting. |
| NOVA explains how to do something instead of doing it | Check **Settings → Actions**: set `NOVA_DESKTOP_CONTROL_ENABLED=true` and restart. Choose a model that can call tools (llama3.2 or mistral in Ollama, or Claude; not phi). Phrase it as a request (“play …”, “open …”). See [docs/ACTIONS.md](docs/ACTIONS.md). |
| Spotify: “Needs a connection” / “no playback device” | Settings → Voice → Spotify → *Connect Spotify*; keep the Spotify app signed in on this PC. |

## Current limitations

- No live AI provider was available in the build environment: Ollama and Anthropic integrations are
  tested against mocked HTTP transports and the real SDK, but a live round-trip is **unverified** until you
  configure one (see RELEASE_NOTES.md).
- Single owner per installation; no account management UI beyond password change.
- Research uses search snippets only; no page fetching. Only SearXNG is implemented.
- No general computer automation. NOVA can act only through its registered tools (Spotify, approved apps, allowed
  websites, project files, media keys, Home Assistant devices, approved commands). These run inside the backend under
  your Windows account, not in an OS sandbox. Small local models are slow and imperfect at choosing tools; see
  [docs/ACTIONS.md](docs/ACTIONS.md#limitations).
- Voice was tested with synthetic speech: in the backend with recorded fixtures and in real Chromium with a fake
  microphone — not with a human voice and a real microphone. The local wake detector and PocketSphinx transcription are
  US-English; local transcription makes frequent word errors (browser recognition or Vosk are more accurate). Vosk was
  only tested with a fake module.
- While NOVA speaks, the waveform shows one bar per spoken word boundary: browsers do not expose synthesized audio.
- Workflow documents without an AI model are scaffolds of your answers, not finished analyses.
- Idea detection on the command center is a transparent English/French phrase rule, not a model; you can always send an
  idea-shaped sentence as a normal message.
- Image understanding needs a vision-capable model; NOVA does not generate images or Figma designs.
- The editor is a plain text area (no syntax highlighting while editing). Binary files are not editable.
- Rate limiting is in-process (fine for one local process).
- Not penetration-tested; designed for local use on `127.0.0.1`.

## Roadmap

1. Extend the 0.4.1 tool-use loop: file edits as change proposals from chat, more integrations behind the same
   registry, and a restricted-account Windows agent for real OS-level isolation.
2. Page fetching for research with SSRF protection; more search providers.
3. Multi-file patches and git-aware commit proposals.
4. Embedding-based search across memory, artifacts and history.
5. Whisper-class local transcription and Piper voices behind the existing voice interfaces; optional desktop shell.
6. Additional providers (OpenAI-compatible endpoints) through the same interface.
7. Workflow research that fetches and cites full pages; AI-assisted task estimates and scheduling.

## License

MIT — see [LICENSE](LICENSE). Change it if you need a different license for your use.
