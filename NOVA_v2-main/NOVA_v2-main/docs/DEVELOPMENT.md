# Development guide

## Day-to-day

```powershell
scripts\dev.ps1          # backend on :8000 with --reload, Vite on :5173 (open http://127.0.0.1:5173)
scripts\test.ps1         # everything before you commit
```

Linux/macOS: run `cd backend && .venv/bin/python -m uvicorn app.main:create_app --factory --reload` and
`cd frontend && npm run dev` in two terminals.

## Adding an API endpoint

1. Define request/response models in `backend/app/schemas/__init__.py` with explicit length limits.
2. Put the logic in a service module (`app/services/…`), not in the route.
3. Add the route to a router in `app/api/…`; depend on `current_user` (which also enforces CSRF) and check
   ownership with `owned_project` or an equivalent helper. Return 404 for other users' resources.
4. Record an audit event for anything that changes data or runs something (`services.audit.record`).
5. Add the typed helper in `frontend/src/api/client.ts` and the type in `api/types.ts`.
6. Test it in `backend/tests/` (happy path, validation, authorization) and, if it has UI, in `frontend/src/test/`.

## Adding an AI provider

Implement `AIProvider` (`app/providers/base.py`): `is_configured`, `default_model`, `status` (live check that
fills `reachable`, `models`, `error`) and `stream` (yield text, raise `ProviderFailure` on errors — never return
placeholder text). Register it in `build_registry`. Add settings to `core/config.py` and `.env.example`. Test it
against a mocked transport.

## Adding an action that needs approval

1. Add the kind to `CATEGORIES` in `app/permissions/__init__.py`.
2. Create the request with `permissions.create_request(...)` from a route; validate parameters first.
3. Execute it in `approve_action` (`app/api/actions.py`) *after* `consume_approval`; re-validate parameters.
4. Describe it for humans in `frontend/src/components/Approval.tsx` (`describeAction`).

## Database changes

Edit `app/models/__init__.py`, then:

```bash
cd backend
mkdir -p /tmp/nova-dev && NOVA_DATA_DIR=/tmp/nova-dev alembic upgrade head
NOVA_DATA_DIR=/tmp/nova-dev alembic revision --autogenerate -m "add something"
```

Review the generated file in `migrations/versions/` (SQLite uses batch mode), run the tests, and commit it.

## Conventions

- Python: type hints everywhere, `ruff` clean, line length 130, no business logic in routers.
- TypeScript: `strict` + `noUncheckedIndexedAccess`; components stay small; shared state goes in `store/`.
- UI copy: sentence case, buttons say what happens ("Apply change", "Approve and run"), errors say what to do.
- Never add a code path that fabricates AI output, search results or command results.

## Dependency updates

Backend pins live in `backend/requirements.txt` (top level) and `backend/requirements.lock` (full freeze,
generated on Linux with `pip freeze` — on Windows install from `requirements.txt`). Frontend versions are exact
in `package.json` with `package-lock.json`. After updating, run the full test script.

## Voice development

- Speech fixtures: `bash scripts/dev/make_voice_fixtures.sh` (needs `festival`, `festvox-us-slt-hts` and `ffmpeg`; test
  tools only) regenerates `backend/tests/fixtures/audio/*.wav` (16 kHz mono PCM16).
- In development, Vite proxies the voice WebSocket (`ws: true`), so the wake phrase works on `http://127.0.0.1:5173`.
- Adding a transcriber: implement `Transcriber` (`accept(frame) -> partial | None`, `result() -> str`) in
  `app/voice/engines.py`, report it in `engine_statuses()` with an honest `available`/`reason`, add it to
  `make_transcriber()`, the `Hello.stt` pattern and `SttEngine` in the frontend types.
- Browser check: `pip install playwright && python -m playwright install chromium`, build the frontend, then
  `python scripts/dev/browser_e2e.py --backend-python backend/.venv/bin/python`.

## Theming

Use the tokens in `src/styles.css` (`--abyss`, `--hull`, `--deck`, `--text`, `--muted`, `--signal`, `--aura`, `--ok`,
`--warn`, `--danger`, the `*-rgb` variants for transparency, glass and shadow tokens). Never hard-code colours in
components; canvas code reads tokens with `getComputedStyle`. `src/test/prefs.test.ts` fails if a text token drops below
WCAG AA contrast on any surface in either theme.
