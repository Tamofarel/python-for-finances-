# NOVA 0.4 implementation report

> **Superseded in part by 0.4.1** (2026-10-18). The “boundaries still open” below about model-driven tool use, tool
> cancellation and deadlines, and approval wiring are now implemented; see [ACTIONS.md](ACTIONS.md). The other
> boundaries remain:
>
> - no separate low-privilege Windows service;
> - live microphone behavior not validated;
> - no voice pause/resume of workflow stages.
>
> Spotify playback was verified live on the target PC in 0.4.1.

## Increment delivered

- Added per-owner `collaborative`, `quiet`, and `step_by_step` interaction modes. The choice persists through NOVA preferences. Quiet mode keeps workflow questions spoken but suppresses nonessential workflow narration; step-by-step advances one workflow stage per continuation.
- Extended the focused workflow voice commands with “explain why”, “change the approach”, and “make a reasonable assumption”. Assumption answers are saved with an explicit assumption label and can be corrected later. Existing skip, continue, stop, one-question-at-a-time, workflow persistence, interruption, and text controls remain in place.
- Added a typed tool registry with JSON schemas, permission levels, argument validation, explicit enabled state, structured outcomes, and audit events. The new authenticated API uses NOVA's normal session and CSRF controls. Protected tools are refused by the registry unless separately implemented through approval handling.
- Added Spotify Authorization Code with PKCE, one-use OAuth state, refresh handling, and Windows DPAPI protection for OAuth tokens. NOVA requests only playback-state, playback-control, and private-playlist read scopes. No Spotify password or client secret is collected or stored.
- Added Spotify Web API playback for tracks and library playlists, playback controls, device discovery, and reported-state verification, with Windows URI/media-key fallback. Spoken Spotify phrases map to the explicit tool schema; “skip” remains a workflow command, while track skip uses “skip track” or “next track”.
- Added an interaction setup note, updated `.env.example`, and generated a release archive with the project packager. The packager excludes `.env`, data stores, logs, and dependency folders.

## Verification

- Backend full suite after Spotify integration: **169 passed, 1 skipped**.
- Backend Ruff: passed.
- Frontend suite: **73 passed**; TypeScript check and production build passed.
- Backend smoke test: steps 1–16 passed, including startup, setup/login, conversation persistence, project files, explicit approval, real allowlisted command execution, audit, CSRF and rejection cases. The later model-backed workflow run did not finish in a reasonable time with the configured Ollama service and was stopped. Those smoke steps remain unverified in this run.
- Automated voice tests use synthetic recordings and mocked browser capture. No physical microphone, real human wake phrase, actual Spotify app/account, live OAuth, or active playback device was tested. Spotify OAuth exchange and playback use mocked HTTP responses in tests.

## Important boundaries still open

- The model does not yet choose tools through a provider tool-calling loop. NOVA currently maps a small set of directly spoken Spotify phrases to the registry.
- Tool execution does not yet expose operation cancellation or enforce per-tool timeouts. Existing command execution still has its established timeout and cancellation protections.
- The Windows adapter is a narrow module inside the NOVA backend process, not a separate low-privilege Windows service. It therefore runs with the backend's OS account privileges; do not treat the registry as an OS sandbox.
- Live Spotify authorization, account eligibility, playback-device behavior, and real-state confirmation remain unverified. The Windows URI/media-key fallback is generic and is always reported as unverified.
- Hands-free wake detection is already local PocketSphinx behind the authenticated local audio stream. Browser microphone permission, secure-context requirements, browser suspension, and OS microphone access still apply; the detector is not a Windows service and cannot wake a closed browser. This run did not validate microphone behavior on the target desktop.
- Voice pause/resume for a running workflow stage is not implemented. The existing UI can cancel a stage; completed workflow stages persist, but cancellation is not a resumable pause checkpoint.

This is a verified incremental archive, not a claim that every item in the broader NOVA 0.4 request is complete. The setup guide explains the implemented behavior and the remaining limitations.

## Hands-free wake follow-up (2026-10-18)

- The first signed-in visit now presents the microphone consent notice automatically. Once enabled, the wake detector re-arms when preferences load on later signed-in sessions and when the tab returns to the foreground. The mic status control remains available to turn it off.
- The default detector accepts both “Hey NOVA” and “NOVA”. The one-word alias has a known false trigger on the suffix of “supernova”; “Hey NOVA” remains the recommended phrase.
- Verification: backend voice suite passed using prerecorded synthetic audio; frontend voice and app tests passed (17 tests); TypeScript check passed. This is automated and fixture-based verification. No real microphone session was available, so hardware/browser recognition and wake-to-spoken-response are still unverified.
