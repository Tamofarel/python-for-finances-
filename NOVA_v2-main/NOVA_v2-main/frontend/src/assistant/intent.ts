// Lightweight, transparent intent detection for the command center (English and French).
// It only decides whether a message *looks like* a new idea to explore; the owner can always send it as a normal
// message instead. No model is involved, so the result is predictable and works without any AI provider.
// Note: JavaScript's \b only knows ASCII letters, so patterns ending in an accented letter use a lookahead instead.

const IDEA_PATTERNS: RegExp[] = [
  /\b(i|we)(\s+|\s*['’]\s*)(want|would\s+like|d\s+like|need|plan|am\s+planning|are\s+planning|intend)\s+to\s+(build|create|make|develop|launch|start|design|write|set\s+up)\b/i,
  /\b(i|we)\s+(have|had|got)\s+an?\s+(new\s+)?(idea|project|concept)\b/i,
  /\bhelp\s+me\s+(build|create|make|develop|launch|design|plan)\b/i,
  /\b(an?\s+)?idea\s+for\s+an?\s+(app|application|product|service|business|website|tool|game|campaign)\b/i,
  /\b(je|nous|on)\s+(veux|voudrais|voudrions|souhaite|souhaitons|aimerais|aimerions|veut|voudrait)\s+(créer|construire|développer|lancer|concevoir|faire|monter|réaliser)\b/i,
  /\b(j['’]ai|nous\s+avons|on\s+a)\s+une\s+(nouvelle\s+)?idée(?=[\s.,:;!?]|$)/i,
  /\baide[-\s]moi\s+à\s+(créer|construire|développer|lancer|concevoir|planifier)\b/i,
];

/** True when the text reads like "I want to build …" / "J'ai une idée …" and is long enough to be an idea. */
export function looksLikeIdea(text: string): boolean {
  const t = text.trim();
  return t.length >= 20 && IDEA_PATTERNS.some((re) => re.test(t));
}

export type VoiceCommand = "skip" | "continue" | "stop" | "yes" | "why" | "change" | "assume";

const COMMANDS: [VoiceCommand, RegExp][] = [
  ["skip", /^(skip|skip (it|this|this one|that|the question)|next question|pass|passe|je passe|question suivante|suivante)$/i],
  ["continue", /^(continue|go ahead|go on|proceed|carry on|explore( everything| it)?|vas-y|allez-y|continue[rz]?|on continue)$/i],
  ["stop", /^(stop|exit|cancel|that's all|leave it|stop the idea|arrête|arrêter|stop là|quitte[rz]?|annule[rz]?)$/i],
  ["yes", /^((yes|yeah|yep|oui)([,\s]+(that's|that is|it's|c'est)\s+(right|correct|it|ça|exact|bien ça))?|correct|that's (right|correct)|exactly|right|exactement|c'est (ça|correct|exact))$/i],
  ["why", /^(why|why is that|explain why|why are you asking|pourquoi|explique pourquoi)$/i],
  ["change", /^(change the approach|change approach|try another approach|change direction|let's do it differently)$/i],
  ["assume", /^(make a reasonable assumption|assume a reasonable default|use a reasonable assumption|assume for now)$/i],
];

/** Recognise a short spoken command used while answering an idea's questions. Longer sentences are answers. */
export function voiceCommand(text: string): VoiceCommand | null {
  const t = text.trim().replace(/^(nova[,\s]+)/i, "").replace(/[.!?,\s]+$/, "");
  if (t.split(/\s+/).length > 5) return null;
  for (const [cmd, re] of COMMANDS) if (re.test(t)) return cmd;
  return null;
}
