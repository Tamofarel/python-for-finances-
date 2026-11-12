import { Mic, MicOff, Square } from "lucide-react";
import { useEffect, useRef } from "react";
import { usePrefs } from "../../store/prefs";
import { useVoice, type TranscriptTarget } from "../../store/voice";

const LOCAL_NOTE = "Speech is transcribed by NOVA on this computer; audio is not stored.";

/**
 * Push-to-talk. Hold the button to talk and release to send. A click, Enter or Space toggles listening on and
 * off instead, which also works for keyboard and switch users.
 */
export function TalkButton({ target = "chat", label = "Talk", compact = false }: { target?: TranscriptTarget; label?: string; compact?: boolean }) {
  const voice = useVoice();
  const pressedAt = useRef(0);
  const startedThisPress = useRef(false);

  const engine = usePrefs((s) => s.prefs.voice.stt_engine);
  const start = () => void voice.startListening(target);
  const disabled = engine === "browser" && !voice.input.supported;
  const note = engine === "browser" ? voice.input.privacyNote : LOCAL_NOTE;
  return (
    <button
      type="button"
      className={`talk ${voice.listening ? "is-listening" : ""} ${compact ? "talk-compact" : ""}`}
      disabled={disabled}
      aria-pressed={voice.listening}
      aria-label={voice.listening ? "Stop listening and send" : `${label} (hold to talk, or click to start and stop)`}
      title={disabled ? "This browser has no speech recognition. Choose a local engine in Settings → Voice." : note}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        startedThisPress.current = !voice.listening;
        pressedAt.current = Date.now();
        if (startedThisPress.current) start();
      }}
      onPointerUp={() => {
        // Held down and released: push-to-talk ends here. A short click keeps listening (toggle mode).
        if (startedThisPress.current && Date.now() - pressedAt.current > 350) voice.stopListening();
      }}
      onClick={(e) => {
        if (e.detail === 0) {
          // Keyboard (Enter / Space) or assistive technology: toggle.
          if (voice.listening) voice.stopListening();
          else start();
        } else if (!startedThisPress.current && voice.listening) {
          voice.stopListening();
        }
        startedThisPress.current = false;
      }}
    >
      {voice.listening ? <MicOff size={compact ? 15 : 22} aria-hidden /> : <Mic size={compact ? 15 : 22} aria-hidden />}
      {!compact && <span>{voice.listening ? "Release or click to send" : label}</span>}
    </button>
  );
}

export function InterruptButton({ busy }: { busy: boolean }) {
  const voice = useVoice();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && (voice.speaking || busy)) voice.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [voice, busy]);
  if (!voice.speaking && !busy) return null;
  return (
    <button type="button" className="btn btn-sm interrupt" onClick={voice.interrupt}>
      <Square size={13} aria-hidden /> Stop {voice.speaking ? "speaking" : "reply"} <kbd>Esc</kbd>
    </button>
  );
}
