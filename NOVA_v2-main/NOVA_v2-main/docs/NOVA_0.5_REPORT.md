# NOVA 0.5 implementation report

## What was already present

The supplied NOVA tree already contains the general provider tool loop, native Ollama and Anthropic tool calls,
typed tool schemas, per-tool deadlines, cancellation contexts, a one-use parameter-hash approval flow, audit events,
voice/chat routing, Windows app/media controls, Spotify, and project workspace confinement. The folder contains no
`.git` directory, so branch, commit history, and a tracked working-tree diff were unavailable for this inspection.

## Added in this increment

- Added an optional visible Playwright browser adapter using a dedicated owner-context and worker thread. Registered
  `browser_open`, `browser_inspect`, `browser_search`, and `browser_open_link` in the existing tool registry.
- Restricted browser requests to approved public HTTPS hosts on port 443. Checks run on the initial URL and every
  routed request, including redirects, subresources, frames, and websocket requests. Private/special-use addresses,
  credential-bearing URLs, downloads, and model-driven form submission are blocked or unavailable.
- Bounded page text, titles, result counts, tool duration, and browser operation waits. Cancellation reports an
  unknown final outcome when navigation may already have reached the site.
- Wrapped page titles, text, and search matches in NOVA's existing `<untrusted_data>` prompt boundary. Added intent
  grounding for browser operations and UI labels for the action records.
- Reused the existing approval mechanism and shortened its expiry from one hour to ten minutes. Exact parameters,
  one-use behavior, on-screen confirmation, and revalidation remain in the existing permission flow.
- Added optional dependency instructions in `backend/requirements-browser.txt` and documented browser boundaries.

## Security boundaries and remaining limits

Playwright and Chromium are optional and were not installed in this environment. The implementation is in-process
under the current Windows account; it is not an OS sandbox. DNS is checked before each routed request, but the
adapter cannot prevent a compromised resolver or a DNS change between validation and connection. Sites using
off-list CDNs may not render fully. Form submission, downloads, screenshots, CAPTCHAs, MFA, purchases, and account
changes are not implemented. The existing model tool set still does not provide general-purpose project file search
or document creation; those actions remain available through the established workspace UI and proposal APIs.

## Verification

- **FAIL** — Full backend test suite executed. One baseline failure persists: `test_command_requires_approval_and_reports_real_result` expects pytest's textual `1 passed` summary, while the
  installed pytest returns its quiet progress dot. The approved command itself exits successfully. One test is skipped.
- **PASS** — Focused browser policy, browser prompt-injection, intent, approval reuse, and approval expiry tests.
- **PASS** — `ruff check backend/app backend/tests`.
- **PASS** — Python byte-compilation of modified backend modules.
- **PASS** — Frontend test suite: 11 files, 78 tests.
- **PASS** — TypeScript check and production frontend build.
- **BLOCKED** — Real Playwright/Chromium and live-site end-to-end checks: the optional Playwright package and Chromium
  binary are absent. Browser security tests use mocked DNS/browser objects and do not establish real Windows network
  enforcement or live website behavior.
- **NOT RUN** — Live microphone, Spotify, or Home Assistant checks; this increment did not change those adapters.

No commit, push, publication, or release archive was created.
