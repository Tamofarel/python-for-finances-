import { Monitor, Moon, Sun } from "lucide-react";
import type { MotionPref, ThemePref } from "../../api/types";
import { Notice } from "../../components/ui";
import { usePrefs } from "../../store/prefs";

const THEMES: { id: ThemePref; label: string; hint: string; icon: typeof Sun }[] = [
  { id: "system", label: "Match my system", hint: "Follows your operating system's light or dark setting.", icon: Monitor },
  { id: "light", label: "Light", hint: "Silver and white surfaces with deep ink text.", icon: Sun },
  { id: "dark", label: "Dark", hint: "Deep blue-grey surfaces with soft light text.", icon: Moon },
];

const MOTION: { id: MotionPref; label: string; hint: string }[] = [
  { id: "system", label: "Match my system", hint: "Uses your operating system's reduced-motion setting." },
  { id: "reduce", label: "Reduce motion", hint: "The core changes colour with NOVA's state but does not animate." },
  { id: "full", label: "Full motion", hint: "The core rotates and reacts to your voice while NOVA works." },
];

export function AppearanceSettings() {
  const prefs = usePrefs((s) => s.prefs);
  const update = usePrefs((s) => s.update);
  const error = usePrefs((s) => s.error);
  return (
    <div className="stack settings-section">
      <fieldset className="choice">
        <legend>Theme</legend>
        <div className="theme-cards">
          {THEMES.map(({ id, label, hint, icon: Icon }) => (
            <label key={id} className={`theme-card theme-card-${id} ${prefs.theme === id ? "selected" : ""}`}>
              <input type="radio" name="theme" checked={prefs.theme === id} onChange={() => void update({ theme: id }).catch(() => undefined)} />
              <span className="theme-swatch" aria-hidden><Icon size={18} /></span>
              <strong>{label}</strong>
              <span className="small muted">{hint}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="choice">
        <legend>Motion</legend>
        {MOTION.map((m) => (
          <label key={m.id} className="choice-row">
            <input type="radio" name="motion" checked={prefs.motion === m.id} onChange={() => void update({ motion: m.id }).catch(() => undefined)} />
            <span><strong>{m.label}</strong><span className="small muted">{m.hint}</span></span>
          </label>
        ))}
      </fieldset>
      <p className="small muted">These preferences are saved to your account and also remembered by this browser so the right theme shows before you sign in.</p>
      {error && <Notice>{error}</Notice>}
    </div>
  );
}

export function ProactivitySettings() {
  const suggestions = usePrefs((s) => s.prefs.suggestions);
  const update = usePrefs((s) => s.update);
  return (
    <div className="stack settings-section">
      <label className="choice-row">
        <input type="checkbox" checked={suggestions} onChange={(e) => void update({ suggestions: e.target.checked }).catch(() => undefined)} />
        <span>
          <strong>Suggest next steps on the command center</strong>
          <span className="small muted">
            Up to three suggestions, such as an approval waiting, an idea's open question or a task that is ready to start.
            They are computed from your projects when the command center is open. NOVA does not monitor anything in the
            background and never acts on a suggestion by itself.
          </span>
        </span>
      </label>
    </div>
  );
}
