# Voice

NOVA can be used entirely by text. Voice adds three things, each of which you turn on yourself:

| Feature | What happens | Where audio is processed |
|---|---|---|
| **Push-to-talk** (*Talk* button) | The microphone opens only while you hold or toggle the button. | Your choice of engine (below). |
| **Wake phrase** (default “hey nova”; also accepts “nova”) | After you accept a one-time notice and allow microphone access, the microphone stays open and NOVA's backend listens for either phrase. | **On this computer**, by NOVA's backend (PocketSphinx). |
| **Spoken replies** | Answers to voice requests are read aloud sentence by sentence while they stream. | The browser's voices — local voices by default. |

The microphone button at the bottom of the sidebar always shows the real state — *off*, *listening for the wake
phrase on this computer*, *listening to you now*, *blocked* or *problem* — and turns the microphone off with one click.

## How it works

```
 browser                                            NOVA backend (same computer)
 ───────                                            ────────────────────────────
 microphone ─► AudioWorklet (16 kHz PCM, 100 ms) ─► WebSocket /api/voice/stream ─► wake detector (PocketSphinx KWS)
                                                                                 └► local transcriber (PocketSphinx / Vosk)
 events ◄────── wake / listening / partial / transcribing / final / empty / error ◄┘
   │
   ├─ wake + browser engine ─► Web Speech API recognises one utterance ─► transcript
   └─ transcript ─► command center (idea? answer? command?) or the conversation pipeline ─► SSE reply ─► speech
```

* The assistant state machine (`frontend/src/assistant/machine.ts`) changes only on these real events:
  `STANDBY → WAKE_DETECTED → LISTENING → TRANSCRIBING → UNDERSTANDING → THINKING → RESPONDING → SPEAKING → STANDBY`,
  plus *researching*, *designing*, *awaiting your answer*, *awaiting approval* and *running an approved action* from
  observed backend state, and *interrupted*, *cancelled* and *error*. *Interrupted*, *cancelled* and a wake phrase with
  nothing said after it return to standby on their own after a few seconds; errors wait for you.
* **Barge-in**: saying the wake phrase, pressing *Talk*, *Stop* or Esc while NOVA speaks stops speech and the reply being
  generated immediately. The partial reply is kept and *Continue the reply* picks it up.
* **Follow-up** (optional): after a spoken answer NOVA listens once more for a few seconds without the wake phrase.
* **Spoken requests to act** (“hey nova, open Spotify and play …”) go through the same assistant pipeline as typed ones
  (0.4.1, [ACTIONS.md](ACTIONS.md)). NOVA speaks each action's own verified result, such as “Playing Cœur De Pirate by
  Tiakola on Spotify”, not the model's paraphrase. When NOVA asks you to choose, answer “the first one” or the title.
* While you answer an idea's questions, short spoken commands work: “skip” / “passe”, “continue” / “go ahead” /
  “vas-y” (explore with the answers so far), “stop” (put the idea aside), “yes” / “oui” (confirm). **Approvals are never
  given by voice** — they need an explicit confirmation on screen.

## Engines

### Choice and evaluation

| Engine | Used for | Runs | Status in NOVA | Why |
|---|---|---|---|---|
| **PocketSphinx 5.0.4** (CMU, BSD) | Wake phrase; basic offline transcription | Locally in the backend | **Default wake detector**; optional transcriber | The pip wheels (≈ 38 MB, available for Python 3.11–3.13 on Windows, macOS and Linux x86-64) contain the US-English acoustic model, dictionary and language model — nothing to download. Keyphrase spotting accepts any phrase made of dictionary words, so the wake phrase is configurable. Low CPU use. Accuracy for free dictation is modest. |
| **Web Speech API** (browser) | Transcription after the wake phrase or while *Talk* is held | Browser vendor | Default transcriber | Best accuracy and many languages. **Chrome and Edge send the audio of what you say to Google or Microsoft**; NOVA says so in the consent notice and in Settings. Not available in Firefox. Never used for the wake phrase. |
| **Vosk 0.3.45** (Apache-2.0) | Better offline transcription | Locally in the backend | Optional (`requirements-voice-extra.txt` + a model you download) | More accurate than PocketSphinx and available for many languages; models are 40 MB–2 GB. **Not verified with a real model in the build environment** (model downloads were blocked there); its adapter is tested with a fake module. |
| openWakeWord | Wake word | Local | Not used | Needs ONNX/TFLite models downloaded from GitHub releases (blocked in the build environment) and a trained model per phrase, so the phrase would not be configurable without training. |
| Porcupine (Picovoice) | Wake word | Local | Not used | Proprietary SDK that requires an access key and online activation; custom phrases are created in Picovoice's console. |
| Whisper / faster-whisper | Transcription | Local | Not used | Model downloads of 75 MB–3 GB and much higher CPU/GPU cost than streaming recognisers; a good candidate behind the same `Transcriber` interface later. Not measured here. |
| Piper | Speech output | Local | Not used | Needs voice model downloads; the browser's local voices are used instead. |

The browser's speech recognition is **not** a wake-word detector and NOVA never uses it as one: wake detection always
happens locally, and raw microphone audio is never streamed to a remote service to look for the wake phrase.

### Measured figures

Measured in the build environment (2 vCPU x86_64 container, Python 3.13) with the synthetic test recordings in
`backend/tests/fixtures/audio` (Festival HTS voice, 16 kHz). Real voices, accents, rooms and microphones will differ.

| Measure | Result |
|---|---|
| Wake detector start-up | 0.20 s |
| Wake detection CPU | real-time factor 0.017 (≈ 2 % of one core while listening) |
| Local transcription (PocketSphinx) | 5.5 s of speech decoded in 0.64 s; 9.4 s in 1.04 s |
| Memory | backend ≈ 166 MB; + wake detector ≈ 43 MB; + local transcriber ≈ 97 MB (peak ≈ 306 MB) |
| Disk | `pocketsphinx` package 38 MB; no model download |
| Wake phrase in a real browser | Chromium with a fake microphone playing the plant recording: armed in 0.8 s, wake heard, transcript turned into an idea workflow (see `scripts/dev/browser_e2e.py`) |

Detections per recording (each positive recording contains the phrase once):

| Phrase / sensitivity | “Hey Nova. What is the weather…” | “Hey Nova. I want to build…” | “Nova, open my projects.” | “…water the plants in the garden…” | “…go over the notes…” | “The supernova was visible…” |
|---|---|---|---|---|---|---|
| “hey nova” — fewer false alarms | 1 | 1 | 0 | 0 | 0 | 0 |
| “hey nova” — balanced (default) | 1 | 1 | 1 | 0 | 0 | 0 |
| “hey nova” — responds more easily | 1 | 1 | 1 | 0 | 0 | **1** |
| “nova” (one word) — balanced | 1 | 1 | 1 | 0 | **1** | **3** |

One-word phrases cause clearly more false alarms; NOVA accepts them but warns you.

Transcription example (PocketSphinx): “Hey Nova. I want to build an app that helps people check whether their plants are
healthy using their phone camera.” → *“hey nova i want to build an app that helps people check whether their **plans** are
healthy using their phone camera”*. This is why NOVA restates every idea and asks you to correct it before anything else.

## Privacy and security

* Microphone audio is accepted **only from this computer** (loopback) unless you set `NOVA_VOICE_ALLOW_REMOTE=true`, only
  from NOVA's own page (Origin check), only for a signed-in owner (session cookie) and only after a CSRF token in the
  first message.
* Audio is processed in memory and **never stored**. Transcripts become your messages like typed text. The audit log
  records when the microphone stream started and stopped (mode, duration, number of wake events) — never audio or words.
* On the first signed-in visit, NOVA shows the hands-free setup notice automatically. Turning the wake phrase on requires
  accepting the notice and granting the browser microphone access;
  the time of consent and enabled setting are stored with your preferences. NOVA automatically re-arms on later signed-in
  sessions and when its tab returns to the foreground. You do not need to press the mic button for each request. Turning
  it off closes the microphone immediately. Browser permission or audio processing may still require an initial click.
* “NOVA” is a one-word local alias and is less reliable: it can false-trigger on a word ending in “nova” (for example,
  “supernova”). The two-word “hey nova” detector keeps the selected sensitivity and remains the recommended option.
* `Permissions-Policy: microphone=(self)`, `camera=()` — NOVA never uses the camera.
* Spoken replies use voices that run on your device by default. Settings → Voice marks online voices; choosing one sends
  the text of replies to the browser's voice service.

## Requirements and permissions

* **Browsers**: Chrome / Edge (all features), Firefox (wake phrase and local transcription; no browser speech
  recognition), Safari 14.1+ (wake phrase and local transcription; Safari's own recognition may use Apple's servers).
  Only Chromium was tested in the build environment.
* `http://127.0.0.1` and `http://localhost` count as secure origins, so the microphone works without HTTPS locally.
  If you serve NOVA to another device, use HTTPS and set `NOVA_VOICE_ALLOW_REMOTE=true` knowingly.
* Allow the microphone for the site when the browser asks (address bar → site settings to change it later).
* **macOS**: System Settings → Privacy & Security → Microphone → allow your browser.
* **Windows**: Settings → Privacy & security → Microphone → *Microphone access* and *Let desktop apps access your
  microphone* on.
* **Linux**: the browser must see an input device (PipeWire/PulseAudio); check with `pactl list sources short`.
* Some browsers start audio processing only after a click on the page; NOVA then shows “Click anywhere so the browser lets
  NOVA listen”. This may also be required after restarting the browser or revoking permission.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NOVA_VOICE_ENABLED` | `true` | `false` disables the voice stream entirely (push-to-talk with browser recognition still works in the page). |
| `NOVA_VOICE_ALLOW_REMOTE` | `false` | Accept microphone audio from other machines. Only behind HTTPS. |
| `NOVA_VOSK_MODEL_PATH` | *(empty)* | Folder of an unpacked Vosk model, to enable the Vosk transcriber. |
| `NOVA_VOICE_MAX_UTTERANCE_SECONDS` | `20` | Longest single utterance that is transcribed. |

Per-owner settings (Settings → Voice): wake phrase (checked against the detector's dictionary), sensitivity, chime,
transcription engine, language, spoken replies, “Okay” acknowledgement, follow-up listening, voice.

### Optional: Vosk

```bash
backend/.venv/bin/pip install -r backend/requirements-voice-extra.txt      # Windows: backend\.venv\Scripts\pip ...
# download and unpack a model from https://alphacephei.com/vosk/models, for example
#   vosk-model-small-en-us-0.15 (≈ 40 MB) or vosk-model-small-fr-0.22 (≈ 41 MB)
NOVA_VOSK_MODEL_PATH=/path/to/vosk-model-small-en-us-0.15                  # in .env, then restart NOVA
```

Settings → Voice then lists *Vosk* as available. The wake phrase itself stays on PocketSphinx (US English).

## Limitations

* Tested with synthetic speech and a fake browser microphone, not with a human voice and a real microphone in the build
  environment. Expect lower accuracy with accents, distance, background noise or music.
* The wake detector is US-English: phrases must use English dictionary words. Local PocketSphinx transcription is
  English-only and makes frequent word errors; use browser recognition or Vosk for dictation quality.
* Echo: if NOVA's spoken reply contains the wake phrase it can wake itself (rare with “hey nova”, likelier with one word).
* The waveform shows the real microphone signal while listening; while NOVA speaks it shows one bar per spoken word
  boundary, because browsers do not expose synthesized audio.
* Browser speech recognition needs a network connection in Chrome/Edge and is unavailable in Firefox.

## Troubleshooting

| Symptom | Fix |
|---|---|
| “Microphone blocked by the browser” | Allow the microphone in the site settings (address bar), check the OS privacy settings above, then press *Try again*. |
| “Microphone audio is only accepted from this computer” | You opened NOVA from another device. Use it on the computer that runs NOVA, or put it behind HTTPS and set `NOVA_VOICE_ALLOW_REMOTE=true`. |
| Wake phrase not recognised | Speak closer, choose *Responds more easily*, or use a two-word phrase. Check that the sidebar says *Listening for “…” on this computer*. |
| Wakes up by itself | Choose *Fewer false alarms* or a longer phrase; avoid one-word phrases. |
| “Local transcription is not enabled” | Choose PocketSphinx or Vosk in Settings → Voice, or keep the browser engine. |
| Vosk shown as unavailable | Install `requirements-voice-extra.txt` and set `NOVA_VOSK_MODEL_PATH` to an unpacked model folder. |
| Nothing is spoken | The browser has no voice for the language, or spoken replies are off (Settings → Voice). Typed requests are never read aloud automatically; use *Read aloud*. |
