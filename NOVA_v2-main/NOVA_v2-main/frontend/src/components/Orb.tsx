import type { Presence } from "../store/chat";

const LABELS: Record<Presence, string> = {
  idle: "NOVA is ready",
  thinking: "NOVA is thinking",
  streaming: "NOVA is responding",
  listening: "NOVA is listening",
  speaking: "NOVA is speaking",
};

/** NOVA's presence: a ring that only moves while NOVA is actually doing something. */
export function Orb({ presence, size = 28 }: { presence: Presence; size?: number }) {
  return (
    <span className={`orb orb-${presence}`} style={{ width: size, height: size }} role="img" aria-label={LABELS[presence]}>
      <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
        <circle cx="20" cy="20" r="15" className="orb-track" />
        <path d="M20 5 A15 15 0 0 1 35 20" className="orb-arc orb-arc-a" />
        <path d="M5 20 A15 15 0 0 0 20 35" className="orb-arc orb-arc-b" />
        <circle cx="20" cy="20" r="4" className="orb-core" />
      </svg>
    </span>
  );
}

export function presenceLabel(p: Presence): string {
  return LABELS[p];
}
