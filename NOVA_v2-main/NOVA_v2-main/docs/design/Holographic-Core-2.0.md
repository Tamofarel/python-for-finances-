# Holographic Core 2.0 — hybrid energy intelligence and humanoid avatar

Status: **Phases A–C done** (audit, specification, standalone prototype). Phases D–F (integration, testing,
final review) are planned below and not started. Nothing in the running application has changed yet.

NOVA's presence has two forms of one intelligence: an **energy core** at rest and while it listens, and a
**humanoid holographic avatar** that materialises out of that core as NOVA thinks, answers and speaks.

Reference: the owner's video (`video_2026-11-13_11-22-23.mp4`) — a luminous sphere wrapped in looping
filaments, from which a line-drawn humanoid figure emerges. Used as direction, not copied: NOVA's figure is a
faceless, abstract bust.

---

## 1. Technical audit (Phase A)

### What exists

| Area | Where | Finding |
|---|---|---|
| Holographic core | `frontend/src/hologram/CoreScene.ts` (three.js 0.186, lazy chunk), `HoloCore.tsx`, `Waveform.tsx` | Wire icosahedron, 5 line rings, 2 arcs, scan sweep, lattice, 160–360 particles. Driven by `setState`, `setLevelSource`, `pulse`. SVG `FallbackCore` when WebGL is missing. No avatar. |
| State | `assistant/machine.ts`, `store/assistant.ts` | One explicit state machine, 16 `AssistantState`s: 10 foreground (event-driven) + 6 ambient (derived from observed backend context). Tested (`machine.test.ts`). **This already is the state model the brief asks for; no second one is needed.** |
| Event wiring | `assistant/wiring.ts`, `store/chat.ts`, `store/voice.ts` | Chat SSE → `SEND / ACCEPTED / FIRST_DELTA / STREAM_DONE / STREAM_ERROR / ABORTED`. Voice → `WAKE / PTT_START / LISTEN_* / SPEAK_*`. Backend polled every 4 s for approvals, running tasks, workflow stage, open question. |
| Stores (zustand 5) | `assistant`, `chat`, `voice`, `prefs`, `providers`, `session` | `CommandCenter` subscribes to the whole `chat` and `voice` stores (`useChat()`, `useVoice()`), so it re-renders on every streamed delta and every word boundary. |
| Voice signals | `voice.level()`, `voice.samples()`, `voice.boundaryPulse` | Real microphone level and time-domain samples while listening; real word-boundary events while speaking. Browsers do not expose synthesized audio, so there is no speech amplitude. |
| Backend stream | `backend/app/api/conversations.py`, `services/agent.py` | SSE events `start`, `delta`, `action`, **`task`**, `error`, `done`. |
| Layout | `components/Layout.tsx`, `pages/command/CommandCenter.tsx`, `panels.tsx` | Left rail (navigation), bare top bar on the command center, centre stage (core, status, waveform, captions, dock), right contextual panels (max 3, only when relevant). Already close to the requested composition. |
| Themes / motion | `styles.css` tokens, `store/prefs.ts` | `data-theme` light/dark/system, `data-motion`; the core switches palette and blending per theme and renders still frames under reduced motion. |
| Tests | `frontend/src/test/*` (12 files, 78 tests before this work) | jsdom has no canvas, so tests exercise the SVG fallback (`role="img"`, name `NOVA: <state label>`). |

### Gaps against the brief

1. **No humanoid form and no transformation** — the main gap; built in Phase C.
2. **The backend's `task` event is dropped by the frontend.** `agent.run_plan` emits step-by-step state
   (`PENDING / ACTION_COMPLETED / ACTION_FAILED / NOT_ATTEMPTED`, `TASK_IN_PROGRESS / COMPLETED / …`) but
   `api/client.ts` `dispatch()` has no `task` branch. Real execution progress is therefore available **without
   any backend change**.
3. **Tool calls during a reply do not show as "executing".** `executing` is only derived from polled command
   tasks; a tool running inside a chat stream (`action` phase `started`) stays "thinking".
4. **Thinking already reacts immediately**: `SEND` is dispatched before the request leaves, giving
   `understanding`. The new visuals must start the materialisation at that state (they do).
5. Error, interrupted and cancelled exist and are distinct; "requires approval" is `awaiting_approval`.

### Performance limits found

- `CommandCenter` re-renders per streamed delta (whole-store subscriptions); `Markdown` re-parses the caption
  each time. Not caused by the hologram, but it competes with it for the main thread. To fix in Phase D with
  selectors, without changing behaviour.
- `Waveform` resizes its canvas (`canvas.width = …`) on every frame, which reallocates the buffer.
- Old core: 13 scene objects, 1 px GL lines (no glow, no thickness control), fixed DPR cap, no frame measurement.

### Must not change

API routes, voice pipeline (wake phrase, STT, streaming speech, barge-in), Spotify/desktop tools, approvals,
audit, tasks, conversations, themes, auth. Accessible name of the core (`NOVA: <state label>`), the
`role="status"` line, all existing tests.

---

## 2. Design specification (Phase B)

### 2.1 Approach

Three options were weighed:

| Option | Verdict |
|---|---|
| CSS/SVG only | Cannot morph ~1,000 particles into a figure smoothly; filters for glow are the expensive kind of blur. Kept as the **fallback** only. |
| Canvas 2D | Feasible, but glow needs per-frame shadow blur or many overdraws on the CPU. |
| **three.js with small custom shaders** (chosen) | Already a dependency and already lazy-loaded. Motion runs on the GPU in vertex shaders; glow is additive sprites and soft-edged ribbons, **no post-processing pass**. No new dependency. |

### 2.2 Colour tokens

| Token | Dark | Light | Use |
|---|---|---|---|
| `blue` (primary) | `#4f7dff` | `#2f5bd8` | core, avatar |
| `violet` (secondary) | `#8f6bff` | `#5b50c9` | orbits, thinking, speaking |
| `hi` (highlight) | `#f4f7ff` | `#16245e` | white-hot lines on dark; deep ink on light |
| `cyan` | `#6fd3ea` | `#0b7d98` | listening / executing accent (existing `--signal`) |
| `warn` | `#f2c46b` | `#a86b00` | approval required (existing) |
| `danger` | `#f28b8b` | `#b4362a` | error (existing, restrained) |
| `muted` | `#56627d` | `#9aa3b3` | interrupted, cancelled |

Background stays the existing `--abyss` family (deep navy → near-black). Dark theme uses additive blending;
light theme uses normal blending with the deeper inks, as the 0.3 core does. Existing CSS tokens are kept; the
hologram palette lives in `holographic.constants.ts`.

### 2.3 Typography

Unchanged: Instrument Sans (UI), JetBrains Mono (figures). State name 1.3 rem / 560; detail line 0.92 rem
muted. No new font, no glowing text.

### 2.4 Component hierarchy

```
frontend/src/hologram/holographic/
  holographic.types.ts       VisualProfile, Frame, Layer, HolographicState (visual family only)
  holographic.constants.ts   palettes, VISUAL_STATE mapping, PROFILES, budgets        (no three.js import)
  figure.ts                  the humanoid as ~10 mirrored strokes + particle targets   (pure, tested)
  EnergySphere.ts            hot centre, bloom sprites, fresnel shell
  OrbitalRings.ts            3 crisp rings, 3 tilted orbits, 4 living filaments (shader-shaped ribbons)
  EnergyParticles.ts         GPU particles: drift ↔ settle on the figure
  HolographicAvatar.ts       self-drawing luminous strokes, aura, scan band
  HolographicScene.ts        composes layers, eases values, quality tiers, frame stats
  lab.ts (+ frontend/hologram-lab.html)   dev-only lab, not in the production build
Phase D (React, planned):
  HolographicCore.tsx        replaces the body of HoloCore.tsx (same props)
  HolographicStatus.tsx      state name + contextual message
  ExecutionOverlay.tsx       current task, steps, approval, verification
```

The brief's `HolographicTransitions`, `HolographicWaveform` are covered by `HolographicScene` (easing) and the
existing `Waveform.tsx` (kept, fixed in Phase D).

### 2.5 States

`AssistantState` remains the only state model. Each state maps to a visual family and a profile:

| Assistant state (real event) | Family | Form (`morph`) | What you see |
|---|---|---|---|
| `standby`, `awaiting_user` | idle | sphere (0) | slow breathing, slow orbits, drifting particles |
| `awaiting_approval` | idle | sphere (0) | same, amber — NOVA waits for you |
| `wake_detected`, `listening` | listening | sphere (0) | brighter; rings and filaments deform with the **real microphone level** |
| `transcribing` | thinking | 0.12 | core expands slightly, first strokes |
| `understanding` (on `SEND`, before the model answers) | thinking | 0.28 | core expands, energy rises from the core |
| `thinking` (SSE `start`) | thinking | 0.46 | active filaments, scan band, torso and shoulders drawn |
| `responding` (first delta) | responding | 0.88 | avatar nearly complete, core settled at its chest |
| `speaking` | speaking | 1 | full avatar, brightest; pulses on **real word boundaries** |
| `executing`, `researching`, `designing` | executing | 0.5–0.7 | avatar present, cyan accent, scan band |
| `error` | error | sphere (0) | dim, restrained red, almost still |
| `interrupted`, `cancelled` | interrupted | sphere (0) | grey, still |

### 2.6 Core-to-avatar transformation

One scalar, `morph`, eased slowly (≈1.5–2 s end to end):

1. `expand` grows the sphere; filament activity (`flow`) and ring speed rise.
2. Particles leave their orbits in a staggered order, pass through the core, and settle on the figure.
3. Strokes draw themselves in order — rising from the core, then torso and shoulders, the head last — each
   with a bright leading edge.
4. From `morph` 0.3 the core group settles at the avatar's chest and shrinks to 46 %, orbits still turning
   around it: the core stays visibly the avatar's heart.
5. Reversing `morph` plays it backwards; the avatar dissolves into the sphere.

### 2.7 Animation rules

- Every animated value eases towards its state profile (`1 − e^(−3.5·dt)`; `morph` slower). Rotations
  accumulate phase, so a speed change never jumps.
- Reactive input is real or absent: microphone level, word-boundary pulses. No random "audio" motion.
- Breathing and drift are ambient and slow (idle must not demand attention).

### 2.8 Responsive behaviour

The scene fills its container (`ResizeObserver`); below aspect 0.9 the camera pulls back so the whole figure
stays in view. Particle size is derived from the drawing-buffer height. Layout breakpoints stay as they are.

### 2.9 Accessibility

- The canvas keeps `role="img"` and `aria-label="NOVA: <state label>"`; state changes are announced by the
  existing `role="status"` line, never by the animation alone.
- Reduced motion (owner preference or OS): one still frame per state change — the form still reflects the
  state, without animation.
- State is never carried by colour alone (label + form + colour).
- Without WebGL the SVG fallback is used.

### 2.10 Performance budget

| Item | Full | Low-power (≤ 4 cores or < 700 px) |
|---|---|---|
| Particles | 7,000 (1 draw call) | 2,500 |
| Draw calls | 16 | same |
| DPR cap | 1.75 | 1.25 |
| Post-processing / blur | none | none |

The scene measures its own frame time; three consecutive seconds above 26 ms average drop the DPR to 1 once.

---

## 3. Prototype (Phase C) — what was built and measured

Run it: `cd frontend && npm run dev`, open `http://localhost:5173/hologram-lab.html`
(`?state=speaking&theme=light&motion=reduce&low=1&bare=1`). The lab's level slider is a lab input only.

**Revision 2 (after the owner's review against the reference video).** The first pass read as a thin mannequin
around a translucent ball. Revision 2 follows the reference more closely:

- Core: a **dark lens with a crisp luminous rim** and a dense particle star at its centre, wrapped in a few
  **large white-hot loops** with a soft blue halo (instead of many thin rings and a translucent shell).
- Environment (`Backdrop.ts`, optional): a slowly moving electric-blue nebula with long, irregular streamers;
  it dims while NOVA materialises.
- Transformation: the particle star unwinds in a **spiral out of the core**, the particles gather into a
  "sand" figure, *then* the line art draws itself and the silhouette darkens in; ≈2 s up, ≈1.5 s back
  (paced, no longer an exponential snap).
- Avatar: an original figure in that style — 44 hair strands, a dark faceless face with a crisp contour,
  neck, collarbones, shoulders, arms, straight suit seams, energy lines running into the core at the chest.

Screenshots (lab, 1100×720): [standby](holographic-core-2.0/standby.jpg) ·
transformation [1](holographic-core-2.0/morph-1.jpg) → [2](holographic-core-2.0/morph-2.jpg) →
[3](holographic-core-2.0/morph-3.jpg) · [thinking](holographic-core-2.0/thinking.jpg) ·
[speaking](holographic-core-2.0/speaking.jpg) · [speaking, light theme](holographic-core-2.0/speaking_theme-light.jpg)

**Revision 3: portrait hybrid (the owner chose it after sharing `portrait-presence.html`).** The avatar
can also take a *portrait* form (`PortraitAvatar.ts`). The energy core and the spiral stay the same. The
particles unwind out of the core and settle on the bright parts of the image. The portrait then dissolves in
outwards from the core, with a luminous edge, and the core settles where the image shows one. Signals:

- **Mouth:** the lower lip slides down in the shader on **real speech-synthesis word boundaries**, never on
  a synthetic curve. (The reference file animated the mouth from a sine formula; that was not carried over.)
- **Eyes and forehead jewel:** they brighten with the real microphone level and spoken words, and the eyes
  also brighten while thinking.
- **Error and interruption:** the scene returns to the sphere.

The portrait is a `PortraitSpec`: the image URL plus anchor points in pixels (eyes, jewel, earrings, core,
lower-lip line). Any image can be swapped in by changing the spec. Until the image loads, or if it fails to
load, the line-art figure is used.

**Licensing:** the reference portrait was downloaded from the web and its rights are unknown. It lives only in
`frontend/hologram-assets/` (git-ignored, served by the dev server to the lab, absent from `dist/`). It has
to be replaced by an image NOVA may distribute before the portrait form ships. Lab:
`/hologram-lab.html?portrait=1`. Measured: 60 fps, 19 draw calls while speaking (16 at rest).

Measured in headless Edge (D3D11, 1100×720, DPR 1) on the development laptop, 1 s sampling windows,
revision 2 with backdrop and 7,000 particles:

| State | Avg frame | Worst frame | Draw calls |
|---|---|---|---|
| standby | 16.7 ms (60 fps) | 17.4 ms* | 16 |
| thinking | 16.6 ms | 20.1 ms | 16 |
| speaking | 16.6 ms | 19.5 ms | 16 |
| speaking, light theme | 16.7 ms | 24.3 ms | 16 |

\* One run of standby showed a single 155 ms frame on the first second after page load (shader compilation);
steady-state frames stay at vsync.

Frames are capped by vsync, so this shows the scene holds 60 fps there; it is **not** a measurement at DPR
1.75, on integrated graphics, or inside the full app while a reply streams. Those belong to Phase E.

Verified: typecheck, 83 frontend tests (5 new in `holographic.test.ts`), production build. The lab page and
the new scene are not referenced by the app, so the application bundle is unchanged.

---

## 4. Implementation plan (Phases D–F)

**Status (2026-11-13): D1, D2 and D3 are done.** The scene fills the command center behind the content and
centres on a reserved `.holo-space`; `HoloCore` is memoised. The SSE `task` event is parsed and kept on the
message; `ACTION_START`/`ACTION_END` events give the `executing` state while a tool runs inside a reply.
`ExecutionPanel` shows the task's steps (running, done, failed, *waiting for you* when the call awaits
approval, a choice or a connection), each call's outcome and its verification. The old `CoreScene.ts` is still
in the tree, unused; it is deleted in D5. No backend change was needed.

**D1 — swap the scene.** `HoloCore.tsx` imports `HolographicScene` instead of `CoreScene` (same methods).
Add the figure to the SVG fallback for avatar states. Delete `CoreScene.ts` once nothing imports it; update
`package_release.py` / `ARCHITECTURE.md` references.

**D2 — real execution state.** Frontend only:
- `api/client.ts`: add `onTask` for the existing SSE `task` event; type it in `api/types.ts`.
- `store/chat.ts`: keep the task state on the message; emit `{type:"task"}` on the stream bus.
- `machine.ts`: add `ACTION_START` / `ACTION_END` events so a tool running inside a reply shows `executing`
  (from the `action` event's `phase`), with tests. No new backend event.

**D3 — ExecutionOverlay + HolographicStatus.** Beside the hologram: current task, steps with their real
status, "waiting for your approval" (links to the existing approval dialog), and *verified / not confirmed*
shown separately from *done* (the `verified` flag already sent). Reuses `ActionCard` wording.

**D4 — interface composition.** Keep rail, stage, dock and contextual panels. Top line on the command center:
NOVA, active model chip, system status. Right panel order: approvals → active task → verification.

**D5 — render hygiene.** Narrow `useChat` / `useVoice` selectors in `CommandCenter`; stop reallocating the
waveform canvas each frame; pause the scene when the tab is hidden.

**E — tests and measurements.** Machine tests for the new events; component tests for the overlay; the
existing suites (frontend, backend, smoke); `scripts/dev/browser_e2e.py` with the fake microphone for
listening; frame timing at DPR 1.75 and with a streaming reply; light/dark/system; reduced motion; resize.

**F — final review** as listed in the brief.

### Known limitations

- Speech output cannot be amplitude-synchronised: browsers do not expose synthesized audio. The avatar
  pulses on real word boundaries (and only on voices that report them). True amplitude needs backend TTS
  audio — a separate decision.
- WebGL ribbons are flat; the avatar turns only a few degrees. A fully 3D figure would need a model asset.
- The figure is drawn in code. Its proportions are the main thing to review.
- The backdrop makes the canvas opaque. Integrating it means the hologram canvas becomes the command center's
  full background (captions and panels sit on top of it) — a Phase D layout decision; without it the scene
  stays transparent over the existing page background.
