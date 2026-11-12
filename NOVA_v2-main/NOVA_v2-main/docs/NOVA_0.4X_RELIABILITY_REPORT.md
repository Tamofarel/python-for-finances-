# NOVA 0.4.x — Reliability, Latency and Voice Recognition Report

## Changes

- Windows app opening now resolves approved names and aliases (including Start Menu shortcuts), uses Windows shell activation without passing model supplied commands, and returns a clear status for unapproved, missing, ambiguous, failed, started, or unconfirmed apps. The app list accepts safe aliases in `NOVA_DESKTOP_APPS`.
- Spotify playback control continues to prefer the Spotify API. Windows media keys are used only when Spotify is already running, and their result is reported as unverified because Windows cannot confirm which app handled the key.
- Exact approved app launches and common Spotify playback controls take a deterministic direct route through NOVA's normal tool permission and audit path. Model and Ollama request timing logs contain durations only, not prompts, transcripts, or generated text. Ollama's keep-alive is configurable and defaults to `10m`.
- Voice language selection now chooses a matching local Vosk model from `NOVA_VOSK_MODEL_PATHS`; browser recognition uses the selected language. PocketSphinx remains English-only. Voice stage timings are recorded without speech content.
- Spoken replies use the existing chat stream and TTS output. When wake detection is armed, NOVA can stop speaking when it hears the wake phrase, accept the next turn, speak its reply, and listen for one follow-up. This is turn-taking with barge-in, not simultaneous full-duplex speech.

## Verification

### Backend and frontend

- Focused backend tests: 89 passed. Coverage includes app aliases and failures, Spotify fallback behavior, direct routing, language-based Vosk selection, Ollama keep-alive payloads, and saved English voice recordings. `ruff check app tests` and Python compilation pass.
- The full backend suite was run. All tests passed except the pre-existing `tests/test_actions.py::test_command_requires_approval_and_reports_real_result` assertion, which expects the subprocess output to contain `1 passed`; pytest's quiet output is `. [100%]` even though that subprocess exits successfully. The failure is in the test's output expectation, not the command's result.
- Frontend Vitest: 78 passed across 11 files. TypeScript typecheck passed. Vite production build passed (5,767 modules); Vite reported its existing large-chunk advisory.

### Windows application activation

Live checks used the actual Windows application registry and process list:

| App | Result | Detail |
| --- | --- | --- |
| Notepad | Started and process verified | Resolved from its executable path |
| Visual Studio Code | Started and process verified | Resolved from its Start Menu shortcut |
| Microsoft Word | Activation failed with `winerror_1312` | Shortcut and executable were found, but this process has no interactive logon session; the response now explains that specific Windows failure |

The Word result is an environment limitation, so Word activation is not reported as successful.

### Latency and recognition observations

- Before the direct route, a benign direct Ollama API probe measured 11,489 ms to first output and 11,763 ms total on a cold request; the next warm request measured 190 ms to first output and 689 ms total. These are model-only observations, not full application end-to-end measurements.
- The direct app route is covered by a test that asserts no model-provider call. A live VS Code launch completed in 102 ms; a live Notepad launch completed in 3,248 ms. Process startup time varies by machine.
- PocketSphinx on the two saved synthesized English recordings measured word error rate (WER) of 0.000 for the weather sentence and 0.053 for the plants sentence. These are small fixture checks, not a representative user accuracy benchmark.
- French model selection is unit-tested, but no French recording or Vosk model is installed in this environment, so French recognition accuracy was not measured. Browser speech recognition was not exercised against a live browser speech service.

## Configuration and documentation

See [NOVA_0.4_SETUP.md](NOVA_0.4_SETUP.md), [ACTIONS.md](ACTIONS.md), and [VOICE.md](VOICE.md) for setup, aliases, language model paths, and engine limits. Set `NOVA_VOSK_MODEL_PATHS` using `en-US=path|fr-FR=path` to configure language-specific Vosk models; `NOVA_OLLAMA_KEEP_ALIVE` controls Ollama model retention.
