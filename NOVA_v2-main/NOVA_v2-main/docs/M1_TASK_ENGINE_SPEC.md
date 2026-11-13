# M1 — Universal Task Execution Engine: specification and implementation plan

Status: **proposed, not implemented.** Nothing in this document exists in the code yet unless the "Today" column says so.
Written after the M0 validation ([M0_VALIDATION_REPORT.md](M0_VALIDATION_REPORT.md)), from the source code as of 2026-11-13.

## 1. Where NOVA is today

One request is handled by `Agent` (`backend/app/services/agent.py`), called from `_stream_generation`
(`backend/app/api/conversations.py`). It takes one of three routes:

| Route | When | What decides the steps |
|---|---|---|
| `plan()` → `run_plan()` | The request is "a report of today in Word" (`intent.document_task`) | A hard-coded list of three `PlanStep`s |
| `deterministic_call()` → `run_direct()` | Exact app-launch and Spotify phrases | One call built by regex |
| `prepare()` → `run()` | Everything else that looks like an action | The model, in a loop of at most 5 rounds and 6 calls |

What already satisfies M1 and must be kept:

| M1 requirement | Today |
|---|---|
| Capability registry, typed inputs, no invented tools | `ToolRegistry` (`app/tools/registry.py`): Pydantic schemas, unknown tools refused, disabled tools carry a reason |
| Permission engine | `PermissionLevel` READ / ROUTINE / APPROVAL with per-call `classify`; approval-level calls become a one-use, hash-bound `ActionRequest` |
| Timeouts, cancellation | Per-tool deadline, 240 s per request, `ToolContext.cancel`, Stop / disconnect / emergency stop |
| Bounded loops | `MAX_ROUNDS`, `MAX_TOOL_CALLS`, `MAX_SAME_CALL` |
| Audit | One `audit_events` row per tool execution, without arguments |
| "One succeeded action is not the task" | `_task_state`, `_conclude`, the `task` SSE event |
| Passing outputs between steps | `PlanStep.arguments(results)`, only in the hard-coded plan |

What is missing:

1. **Only one request has a plan.** Any other multi-step request depends on the model deciding to continue.
2. **Completion is checked by a special case.** `_missing_document` knows about Word only; no other objective is verified.
3. **Task state is not a first-class thing.** It is a dict built on the fly, with states that differ from the target
   vocabulary (`PENDING`, `NOT_ATTEMPTED`; no planning, verifying, awaiting-approval or cancelled task state). A task
   waiting for approval is reported as `TASK_IN_PROGRESS`.
4. **Task state is not stored.** Only the per-action records are saved on the message. After a refresh the outcome of
   the task is gone.
5. **The frontend ignores the `task` event.** `streamPost` (`frontend/src/api/client.ts`) handles `start`, `delta`,
   `action`, `error`, `done`.
6. **Approval ends the task.** An approved call runs alone from `/api/actions/{id}/approve`; steps after it never run.
7. **No retry.** A transient failure in a read-only step ends the task.
8. **Cancellation leaves no task outcome.** The message becomes `cancelled`; no `TASK_CANCELLED` is emitted or stored.

## 2. Design

The smallest change that closes those gaps: turn the plan/step/outcome logic that is inside `Agent` into one small
engine that all three routes use. No new process, no queue, no workflow DSL.

### 2.1 Components

New package `backend/app/engine/`:

| Module | Responsibility | Built from |
|---|---|---|
| `model.py` | `TaskState`, `ActionState`, `Step`, `TaskRun` (dataclasses) | New |
| `planner.py` | Turns a request into a `TaskRun`: a matching *recipe*, a single direct call, or a model-driven run | `Agent.plan`, `Agent.deterministic_call`, `intent.py` |
| `executor.py` | Runs steps in dependency order, passes outputs, applies deadlines, retries, cancellation, approval pauses | `Agent.run_plan`, `Agent._run_call`, `Agent._execute` |
| `verifier.py` | Per-step and whole-objective checks | `Agent._conclude`, `Agent._missing_document` |
| `store.py` | Saves and loads `TaskRun` rows | New |

`Agent` stays as the model loop (`_run_model`) and calls the executor for each tool call, so the guards that exist
today (grounding, held-back preamble, text tool calls, the nudge) are not rewritten.

The permission engine and the capability registry are the existing `ToolRegistry` and `app/permissions`. They get two
small additions, not replacements:

- `Tool.produces: type[BaseModel] | None` — the shape of `data` on success, so a recipe can refer to a field of an
  earlier step and the reference can be checked when the recipe is registered, instead of failing with a `KeyError`
  mid-task. Added to the tools a recipe reads from, starting with `activity_today` and `word_document`.
- `ToolRegistry.schemas()` (already served by `GET /api/tools`) reports `produces` alongside `parameters`.

### 2.2 States

```
TASK_PLANNING → TASK_IN_PROGRESS ⇄ AWAITING_APPROVAL
                       ↓
                TASK_VERIFYING → TASK_COMPLETED | TASK_PARTIALLY_COMPLETED | TASK_FAILED
any non-final state → TASK_CANCELLED
```

Steps: `ACTION_PENDING → ACTION_RUNNING → ACTION_COMPLETED | ACTION_FAILED | ACTION_SKIPPED`.

Rules, each enforced in `executor.py` / `verifier.py` and each with a test:

- A step is `ACTION_COMPLETED` only when its tool returned `succeeded`. `unverified` is `ACTION_FAILED` with the
  reason kept (this is today's behaviour).
- A step whose dependency did not complete is `ACTION_SKIPPED`, never run.
- The task is `TASK_COMPLETED` only if every required step completed **and** every objective check passed.
- Some steps completed, others not → `TASK_PARTIALLY_COMPLETED`. None → `TASK_FAILED`.
- `needs_input` / `needs_authorization` end the run as `TASK_PARTIALLY_COMPLETED` or `TASK_FAILED` with the question
  as the reply; the follow-up message starts a new run (today's `follow_up_of` behaviour, unchanged).

Renaming `PENDING` → `ACTION_PENDING` and `NOT_ATTEMPTED` → `ACTION_SKIPPED` changes the payload of the `task` SSE
event. No frontend code reads that event today, so nothing breaks; the four backend tests that assert the old names
are updated in the same change.

### 2.3 Step and recipe

```python
@dataclass(frozen=True)
class Step:
    id: str                          # "activity", "write", "show"
    label: str                       # shown to the owner
    tool: str
    arguments: Callable[[dict[str, ToolResult]], dict[str, Any]]   # built from completed steps, by id
    depends_on: tuple[str, ...] = ()
    required: bool = True            # False: its failure makes the task partial, not failed
    retries: int = 0                 # only honoured when the call's permission level is READ

@dataclass(frozen=True)
class Recipe:
    name: str
    matches: Callable[[str], bool]   # on the owner's request text
    steps: tuple[Step, ...]
    checks: tuple[Check, ...] = ()   # whole-objective checks, run in TASK_VERIFYING
```

`depends_on` replaces "position in the list". Steps with no unmet dependency run in order, one at a time. Running
independent steps in parallel is **not** in M1: every tool today talks to one desktop or one account, and no current
request needs it.

Recipes in M1 (each replaces existing code, none is speculative):

| Recipe | Replaces |
|---|---|
| `today_report` | `Agent.plan` |
| `open_app` | the app branch of `deterministic_call` |
| `spotify` | the Spotify branch of `deterministic_call` |

A model-driven request has no recipe: the executor creates a step for each tool call as the model makes it, under the
same limits as today.

### 2.4 Verification

- **Per step**: the tool's own result. `succeeded` already means "checked" for Spotify (player state), Word (file
  read back, window seen) and apps (process seen). The engine adds nothing here.
- **Whole objective**: `Check` objects. M1 ships one, `requires_tool("word_document")`, which is `_missing_document`
  moved and generalised: *the request asks for an outcome that only tool X produces, and no call to X completed.* The
  existing one-time nudge to the model stays, driven by the failed check instead of a Word-specific branch.

A general "did the model achieve the goal" judgement by a second model call is **not** in M1. It costs a model round
on every request and a small local model is not reliable at it. It is revisited in M4, when adapters declare a
`verify` method.

### 2.5 Approval inside a task

Today a call at APPROVAL level returns `awaiting_approval` and the request ends. In M1:

1. The executor stores the run as `AWAITING_APPROVAL` with the `ActionRequest` id on the waiting step.
2. `POST /api/actions/{id}/approve` runs the approved call as now, then, if the request belongs to a stored run,
   records the result on that step and runs the remaining steps in the same HTTP request (bounded by the same
   deadlines). The response gains an optional `task` field; existing fields are unchanged.
3. Reject, expiry (10 minutes) and the emergency stop mark the step `ACTION_FAILED` and conclude the run.

The model is not called again after an approval. Only recipe steps continue; a model-driven run that hits an approval
ends as it does today. This keeps the approved action exactly the one the owner saw.

### 2.6 Persistence

Migration `0005_task_runs`:

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `owner_id` | FK users, cascade | |
| `conversation_id` | FK conversations, cascade, nullable | |
| `message_id` | FK messages, set null, nullable | the assistant reply |
| `objective` | text | the owner's request, as written |
| `recipe` | string(40), nullable | null for model-driven runs |
| `status` | string(32) | a `TaskState` |
| `steps` | JSON | id, label, tool, state, status, message, verified, duration_ms, action_request_id |
| `created_at`, `updated_at`, `finished_at` | datetime | |

Arguments are not stored in `steps` (they can contain document text); they stay in `messages.actions` as today.
`MessageOut` gains an optional `task` field so a reloaded conversation shows the outcome. At startup,
`_recover_interrupted_work` marks runs left in a non-final state as `TASK_FAILED` ("interrupted"), as it does for
tasks and messages.

### 2.7 Events

The existing SSE stream, no new channel. The `task` event carries `{id, status, steps[]}` and is sent on every state
change. M1 only makes the frontend *store* it (`onTask` in `streamPost`, a `task` field on the message in
`store/chat.ts`); drawing the task panel is M5.

## 3. Implementation plan

Each step is one reviewable change with its tests, and leaves the suite green.

| # | Change | Tests |
|---|---|---|
| 1 | `engine/model.py`: states, `Step`, `TaskRun`; `Agent._task_state` / `_conclude` return them. Rename the two step states. | Existing task tests, updated names |
| 2 | `engine/executor.py`: move `run_plan` and `_run_call` logic; `depends_on`, `ACTION_SKIPPED`, `required`, READ-only retries | Dependency order; skipped after a failed dependency; optional step; retry only for READ; limits still apply |
| 3 | `engine/planner.py`: `Recipe` registry; port `today_report`, `open_app`, `spotify`. `Agent.plan` and `deterministic_call` become lookups. | Every existing routing test in `test_agent.py` and `test_intent.py` passes unchanged |
| 4 | `engine/verifier.py`: `Check`, `requires_tool`; remove `_missing_document` | The four Word nudge / partial-completion tests pass unchanged |
| 5 | Migration `0005`, `engine/store.py`, `MessageOut.task`, startup recovery | Run saved and reloaded; survives restart; interrupted run marked failed; cascade on conversation delete |
| 6 | `TASK_PLANNING`, `TASK_VERIFYING`, `TASK_CANCELLED` emitted and stored; disconnect and emergency stop conclude the run | Cancel mid-step → `TASK_CANCELLED`, later steps `ACTION_SKIPPED` |
| 7 | Approval continuation (§2.5) | Approve → remaining steps run; reject / expire / emergency stop → concluded; replayed approval refused |
| 8 | `Tool.produces` on `activity_today`, `word_document`; recipe references checked at registration | A recipe reading a field its source does not produce fails at startup, not mid-task |
| 9 | Frontend: `onTask`, `Message.task` type and store field | Store keeps the last task state; reload shows it |
| 10 | Docs: `ARCHITECTURE.md`, `API.md`, `DATA_MODEL.md`, `ACTIONS.md`, `CHANGELOG.md` | — |

Then the M0 live procedure is repeated (Word report twice, standalone "Open Word", "Play Bandit by Juice WRLD") and
its results recorded as *tested in a real environment*.

## 4. Acceptance criteria

- The three existing routes produce the same replies and action records as before (regression suite unchanged apart
  from the two renamed states).
- A recipe step that fails stops the steps that depend on it, and the reply names what was done, what failed and what
  was skipped.
- No path sets `TASK_COMPLETED` while a required step or an objective check has not passed.
- A task survives a page reload and a backend restart with its final state.
- Cancelling a running task yields `TASK_CANCELLED` within 3 s.
- A task with an approval step continues after approval and never runs anything other than the approved call.
- No new dependency. One migration. No change to existing API fields.

## 5. Risks and open decisions

| Item | Position taken | To confirm |
|---|---|---|
| Model-written plans (the model returns a list of steps up front) | Not in M1. The local default model (llama3.2, 3B) already needs four guards to make single tool calls reliably. Recipes plus the existing loop cover today's requests. | Revisit when a stronger default model is in use |
| Parallel steps | Not in M1 (§2.3) | — |
| Approval continuation runs in the approve request | Simple and bounded; a long remaining chain would hold that request open (at most the 240 s request deadline) | Acceptable for now? |
| `unverified` counts as a failed step | Kept from today. A Word document that opens slowly therefore makes the task "partial"; M0 raised the wait to 30 s for that reason. | — |
| Renamed step states in the `task` event | No reader exists today | — |
