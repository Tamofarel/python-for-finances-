# M0 — Stabilization and validation report

Date: 2026-11-13. Code: commit `0b62fef` ("feat:auto compile documents") plus the two fixes listed in §4.
Machine: the owner's Windows 11 laptop (15.7 GB RAM, C: 100 % full with 4.2 GB free).

Every result below says how it was obtained:

- **Live** — NOVA's real code against the real production database, the real Windows session, real Word and the
  owner's real Spotify account.
- **Mocked** — the automated suite, where Windows, Word, Spotify and the AI provider are fakes.
- **Not validated** — not run, with the reason.

## 1. Summary

| Area | Result | How |
|---|---|---|
| Word report: records → document → saved → read back → opened → location returned | Works. One defect found and fixed (§4.1) | Live |
| Repeated report requests do not overwrite | Confirmed | Live |
| Standalone "Open Word" creates no document | Confirmed | Live |
| "Play Bandit by Juice WRLD": search → device → transfer → play → verified state | Works | Live |
| Spotify works with desktop control off | Confirmed | Live |
| Activity retrieval uses the configured database and local-day boundaries | Confirmed | Live (read-only) |
| Frontend tests, typecheck | 78 passed in 11 files; typecheck clean | Mocked |
| Backend lint | 1 error found and fixed (§4.2) | — |
| Backend tests | 390 passed, 1 skipped, 0 failed (§5) | Mocked |
| The same requests through the HTTP API and the UI of a running NOVA | **Not validated** (§3) | — |

**The NOVA instance that is running on this laptop does not contain the Word document feature.** It was started on
2026-11-12 at 15:17 (process 31920); `agent.py`, `intent.py`, `activity.py` and `documents.py` were last written
between 15:44 and 15:52. Until it is restarted, "open Word and report what we did today" behaves as recorded in the
database at 15:23: it opens Word, and the model writes a summary in the chat from memory. Restart NOVA to get the
behaviour validated here.

## 2. Architecture audit

Source code as read on 2026-11-13. Earlier reports in `docs/` were not relied on.

| Capability | Current implementation | State |
|---|---|---|
| Word documents | `backend/app/tools/documents.py`: `WordDocuments` writes a .docx with python-docx into one folder (`NOVA_DOCUMENTS_DIR`, default `Documents\NOVA`), reads the saved file back and compares it, then opens it and looks for its Word window. Tool `word_document` (create / open / read / status). Replacing a file needs on-screen approval. | Working (live) |
| Activity history | `backend/app/services/activity.py`: `today_activity` builds the report from `messages` and their stored action results; no model involved. Tool `activity_today`. | Working (live) |
| Spotify | `backend/app/tools/spotify.py`: OAuth PKCE, DPAPI token file `data/spotify-token.bin`, Web API search → device pick → transfer → play (album context, then plain) → poll player state. Tool `spotify_control`, enabled whenever a client ID is set, independent of desktop control. | Working (live) |
| Task orchestration | `backend/app/services/agent.py`: three routes — one hard-coded 3-step plan (today's report in Word), regex-routed single calls (apps, Spotify), and a bounded model tool loop. A `task` event reports `TASK_IN_PROGRESS` / `COMPLETED` / `PARTIALLY_COMPLETED` / `FAILED`. | Partial: one plan only; task state is not stored; see [M1 spec](M1_TASK_ENGINE_SPEC.md) |
| Conversations | `backend/app/api/conversations.py`, tables `conversations` / `messages`: create, list, search (titles and text), rename, delete, retry, SSE streaming; history capped at 40 messages / 60 000 characters per request. Frontend: `store/chat.ts`, `ChatPage`, `HistoryPage`. | Working (mocked). Each conversation already has its own history. No sidebar on the command center, no archive, no per-conversation summary. |
| Memory | Table `memory_records` (preference / fact / note, general or per project, explicit saves only). `build_system_prompt` injects up to 100 enabled records in every request. | Basic: no retrieval by relevance, no memory from past conversations, no conflict detection. 0 records in production. |
| Desktop control | `backend/app/tools/windows_agent.py`, `desktop_os.py`: allowlisted apps (`os.startfile`, no arguments), allowlisted websites, project files with safe extensions, media keys. Off unless `NOVA_DESKTOP_CONTROL_ENABLED=true` (it is `true` here). | Working (live for Word and Spotify launch) |
| Approval | `backend/app/permissions/__init__.py`, `api/actions.py`: `ActionRequest` bound to a hash of the exact parameters, single use, 10-minute expiry; emergency stop cancels running tools and rejects pending tool calls. | Working (mocked). An approved call runs alone: a task does not resume after it. |
| Audit | `services/audit.py`, table `audit_events`: one row per tool execution with status, risk, duration, verified; arguments are not stored. 52 rows in production before this validation. | Working (live: this validation's calls were recorded) |
| Streaming | SSE on `POST /api/conversations/{id}/messages`: `start`, `delta`, `action`, `task`, `error`, `done`. WebSocket only for voice (`api/voice.py`). | Working. **The frontend does not read the `task` event** (`frontend/src/api/client.ts`). |
| Holographic Core | `frontend/src/hologram/` (three.js scene, SVG fallback), state machine in `frontend/src/assistant/`. | Working (mocked); not changed |

## 3. Validation plan and method

The running NOVA server was left untouched: it holds the owner's session, and restarting it or logging in was not
mine to do. Instead, the real `Agent` was run in a separate Python process built exactly as `create_app` builds it:
the real settings from `.env`, the production database `data/nova.db`, the real tool registry, the real document
folder, real Word and the owner's Spotify token.

Covered: intent routing, planning, tool validation, permission check, execution, verification, task outcome, audit.
Not covered: the HTTP route, login and CSRF, SSE framing, persistence of the reply in `messages`, the UI. Those
layers are exercised by the automated suite with fakes only.

Side effects of the live runs, all intended and all still in place:

- Two documents in `C:\Users\exp\Documents\NOVA` (the folder did not exist before).
- Word is open with both documents. Spotify was started on *Bandit*, then paused.
- Nine rows added to `audit_events` (`tool.activity_today` ×2, `tool.word_document` ×4, `tool.open_app` ×1,
  `tool.spotify_control` ×2). No conversation or message was written.

## 4. Results

### 4.1 Word — "Open Microsoft Office Word and report what we did today."

| Required step | Run 1 (Word closed) | Run 2 (Word open) |
|---|---|---|
| 1. Records read from the production database | `activity_today` succeeded: 0 requests today (see below) | same |
| 2. Factual report prepared | Title, generation time, "NOVA has no recorded requests for today." | same |
| 3–5. DOCX created, content inserted and formatted, saved in the NOVA folder | `NOVA activity report 2026-11-13.docx`, 36 776 bytes | `NOVA activity report 2026-11-13 (2).docx` |
| 6–7. Saved file read back and verified | By NOVA, and again independently with python-docx: 4 paragraphs, title and summary present | same |
| 8. Opened in Word | **Reported "not seen" after 15.7 s**; the window appeared moments later | Seen after 6.2 s |
| 9. File location returned | Full path in the reply | Full path in the reply |
| Task outcome | `TASK_PARTIALLY_COMPLETED` | `TASK_COMPLETED` |

The report is empty of requests because it was generated on 13 November and the last recorded request is from
12 November. That is the correct answer for "today". Run against the same database with the clock set to
12 November 23:59, the same function returns the 8 requests and 7 actions of that day, with local times
(for example the 14:23 UTC request is listed at 15:23).

**Defect found: a slow cold start of Word was reported as a partially completed task.** Word's splash window
("Opening - Word") was still showing when NOVA stopped waiting at 15 s. The document did open. NOVA did not claim
success it could not confirm, which is the intended rule, but the limit was too short for this laptop.
Fix: `OPEN_WAIT_SECONDS` 15 → 30 in `backend/app/tools/documents.py`, still bounded by the tool's 40 s deadline.
Test added: `test_a_slow_cold_start_of_word_is_waited_for`. **The fix was tested with a fake Word only**; repeating
the live cold start needs Word closed, which would have meant closing the owner's Word.

No overwrite: after run 2 the first file's SHA-256 was unchanged and the second file has a new name.

Standalone **"Open Word"**: no plan, one `open_app` call, Word process confirmed, `TASK_COMPLETED`, and no new file
in the document folder.

Observation, not changed: documents written by python-docx open in Word's *Compatibility Mode*.

### 4.2 Spotify — "Play Bandit by Juice WRLD."

Run with a registry built with desktop control **off** (`open_app` disabled, `spotify_control` enabled).
Starting state: nothing playing, one device (`LAPTOP-EXPERTIZ`, Computer, not active).

| Required step | Observed |
|---|---|
| 1. Search | `GET /search` 200 |
| 2. Correct track | *Bandit (with YoungBoy Never Broke Again)* — Juice WRLD, YoungBoy Never Broke Again |
| 3. Devices discovered | `GET /me/player/devices` 200 |
| 4. Device selected | `LAPTOP-EXPERTIZ` |
| 5. Playback transferred | `PUT /me/player` 204, then the device reported active |
| 6. Playback started | `PUT /me/player/play` 204 (album context with track offset) |
| 7. State verified | NOVA polled `GET /me/player` until it showed the track playing (9.8 s in total). A separate check 3 s later: `is_playing` true, same track, device active, progress 3 883 ms |
| 8. Result reported | "Playing Bandit (with YoungBoy Never Broke Again) by Juice WRLD, YoungBoy Never Broke Again on Spotify (LAPTOP-EXPERTIZ)." `TASK_COMPLETED` |

"pause the music" afterwards: paused and confirmed in 1.6 s.

The same request on 12 November at 14:28 ended "accepted but not confirmed"; the fix committed that day (transfer
first, then play inside the album) is what makes it pass now.

Lint: `ruff` reported one error, the import order in `backend/app/tools/spotify.py`, introduced with that fix.
Corrected; no behaviour change.

### 4.3 Database

- `get_settings()` resolves to `sqlite:///…/NOVA_v2-main/data/nova.db`; schema at migration `0004`.
- 1 owner, 9 conversations, 52 messages (2026-10-21 to 2026-11-12), 0 memory records, 0 approval requests.
- Day boundary: local midnight converted to UTC (`activity.py`), checked on the real rows as described in §4.1.
- No temporary database was used for any result marked *Live*.

## 5. Regression suite

| Suite | Result |
|---|---|
| Frontend `npm test` | 78 passed, 11 files, 0 failed, 0 skipped |
| Frontend `npm run typecheck` | Clean |
| Backend `ruff check app tests` | Clean after the fix in §4.2 |
| Backend unit tests for the changed areas (`test_documents`, `test_spotify`, `test_intent`) | 130 passed |
| Backend full suite (391 tests, collected before the test added in §4.1; that test passes in the row above) | **390 passed, 0 failed, 1 skipped**, 12 min 27 s as timed by pytest. Skipped: `test_workspace.py::test_symlink_escape_rejected` ("symlinks not supported here", a Windows limitation without developer mode). The failure listed in earlier reports (`test_command_requires_approval_and_reports_real_result`) did not occur. |
| Frontend production build, `scripts/smoke_test.py`, `scripts/dev/browser_e2e.py` | Not run (see below) |

**Configuration problem: the backend suite is slow on this machine.** Tests that build the application take about
4.4 s each before the test itself starts (up to 12.8 s for the slowest setups), of which 1.9 s is SQLite running the
migrations (about 14 ms per statement). The disk is full, 0.8 GB of RAM was free (VS Code alone holds 5.5 GB) and
Avast scans temporary files. The whole run takes more than the 10 minutes a foreground command is allowed here: two
earlier attempts were stopped at that limit because they looked hung; they were not. Run it in the background with
its output in a file.

Not run, to avoid loading the machine further: the frontend production build, the smoke test (it starts a second
backend) and the browser end-to-end script (needs Playwright, which is not installed).

Windows-specific limits seen: none blocked a test. Earlier reports mention Word failing to start with error 1312
when NOVA runs without an interactive session; in this interactive session Word started normally.

## 6. Open items

| Item | Why it matters | Where it goes |
|---|---|---|
| Restart the running NOVA | It runs code from before the Word feature | Owner |
| Repeat §4.1 and §4.2 through the UI after the restart | The HTTP, SSE and UI layers were not validated live | Owner, or M0 follow-up |
| Live cold-start test of the 30 s wait | The fix is tested with a fake only | Next time Word is closed |
| The frontend ignores the `task` event; task outcome is not stored | The UI cannot show "partially completed" | M1 (storage), M5 (display) |
| A model reply stored on 12 Nov includes the internal note "[Actions taken by NOVA for this reply: …" | The history annotation can leak into replies with a small model | M1 |
| Free disk space and memory | Test runs and Word cold starts are slow | Owner |
