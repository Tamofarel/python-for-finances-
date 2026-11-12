# NOVA 0.4 desktop controls and verification

> **0.4.1:** requests to act now go through the assistant itself. The model picks a registered tool, NOVA validates
> it, runs it with a deadline, verifies the outcome, and reports it truthfully. Typed and spoken requests share the
> same pipeline. Full details, the Home Assistant integration, the emergency stop and the live test record are in
> [ACTIONS.md](ACTIONS.md). This page covers setup.

## Spotify controls

The Windows integration uses Spotify's Web API for:

- authorized track, playlist and artist search and playback;
- pause/resume, next/previous and volume;
- verification from Spotify's reported state.

If the Spotify app is closed, NOVA opens it and waits for its playback device. If the Web API is not connected, NOVA
answers **Needs a connection** and offers *Connect Spotify*. Playback controls (pause, next…) then fall back to Windows
media keys, reported as **Not confirmed**.

The integration never invokes a shell and never accepts executable names or file paths from a model.

Enable desktop actions with `NOVA_DESKTOP_CONTROL_ENABLED=true`, then restart NOVA. You also need a model that can
call tools: llama3.2 or mistral in Ollama, or Claude.

- The authenticated `GET /api/tools` catalog reports which tools are enabled and why the others are not.
- Tool runs are audited without recording the request text.

To connect Spotify:

1. Create a Spotify Developer app and copy its client ID.
2. Add `NOVA_SPOTIFY_CLIENT_ID=...` to `.env`. Do not add a Spotify password or client secret.
3. In the Spotify Developer Dashboard, add the exact redirect URI `http://127.0.0.1:8000/api/spotify/callback`
   (replace `8000` if `NOVA_PORT` is different).
4. Restart NOVA, sign in, then open **Settings → Voice → Spotify → Connect Spotify**. Review and approve the
   requested Spotify scopes on Spotify's own page.

NOVA uses Authorization Code with PKCE and a one-use state value. OAuth tokens are protected with Windows DPAPI
for the current OS account in `NOVA_DATA_DIR`; NOVA never stores account passwords. The API needs an authorized
account and a ready playback device. Spotify may require Premium for playback-control endpoints; check the actual
Spotify response if an operation is refused. Spotify describes the supported [PKCE flow](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
and [start playback endpoint](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback).

You can type or say requests such as:

- “Hey NOVA, open Spotify and play Tiakola — Vie de Pirate”
- “play Starboy by The Weeknd”
- “play my coding playlist”
- “pause the music”
- “skip to the next track”
- “turn the volume down”

When the title is ambiguous or doesn't exist, NOVA lists Spotify's top results and asks which one to play. Answer
“the first one”, “number two” or the title. Playback is reported as successful only after Spotify's player state shows
the requested item playing.

Desktop controls are disabled by default and are not covered by workspace or command-runner permissions. Only the
schema-validated tools listed in [ACTIONS.md](ACTIONS.md) are registered. Safety-critical operations (commands, locks,
heating, covers…) go through NOVA's one-use approval flow and are never approved by voice.

Choose a collaboration style under **Settings → Voice → Collaboration style**. Collaborative mode uses short
spoken workflow updates, quiet mode keeps progress visible while speaking questions and answers, and step-by-step
mode advances one workflow stage each time you continue. Text input and buttons remain available.

## Voice and browser limitations

Wake phrase spotting and local transcription use PocketSphinx in NOVA's backend. Microphone PCM is sent only to the
local NOVA backend by the browser's authenticated WebSocket; it is not sent to a cloud wake detector. Browser speech
recognition, when selected for dictation after wake, can send that utterance to the browser vendor. Select PocketSphinx
or Vosk to keep transcription local.

On the first signed-in visit, NOVA opens a one-time hands-free setup notice. Accept it and grant browser microphone
permission. After that, the saved wake setting automatically arms on signed-in app startup and when the tab returns to
the foreground; you do not need to click the mic icon for every request. Say “Hey NOVA” or “NOVA”, then speak your
request. “NOVA” alone can false-trigger on words ending in “nova”, such as “supernova”, so “Hey NOVA” is recommended.

The page must be served in a secure context (localhost qualifies). Browsers can suspend audio capture until a user
gesture, particularly after a restart or when a tab was backgrounded; NOVA exposes suspended, denied and failed states
and offers Try again. The browser window must remain open and the OS must allow microphone access. The wake detector is
not an always-running Windows service and cannot listen while NOVA is closed.

## Verification record

The automated voice tests use saved synthetic recordings and mocked browser capture. They validate both default wake
phrases and session re-arming, but they do not prove recognition with this user's real microphone or accent.

On 2026-10-18 the Spotify action was verified live on the target Windows PC:

- NOVA's chat API and agent, local llama3.2, the owner's Spotify account and the real launcher;
- the requested song was played and **confirmed from Spotify's reported player state**.

The spoken (microphone) path to the same pipeline was not exercised live. See [ACTIONS.md](ACTIONS.md#testing) and
[NOVA_0.4_REPORT.md](NOVA_0.4_REPORT.md).
