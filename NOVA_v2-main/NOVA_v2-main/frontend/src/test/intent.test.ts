import { describe, expect, it } from "vitest";
import { looksLikeIdea, voiceCommand } from "../assistant/intent";

describe("idea detection", () => {
  it("recognises ideas in English and French", () => {
    for (const t of [
      "NOVA, I want to build an app that helps people determine whether their plants are healthy using their smartphone camera.",
      "We'd like to create a booking tool for our yoga studio",
      "I have an idea for my bakery: a pre-order website",
      "Help me plan a marketing campaign for a new coffee brand",
      "Je veux créer une application pour suivre mes plantes",
      "J'ai une idée de service de location de vélos",
    ]) expect(looksLikeIdea(t)).toBe(true);
  });

  it("leaves ordinary questions alone", () => {
    for (const t of [
      "What is the weather like today?",
      "Explain the difference between SSE and WebSockets",
      "I want to know why my tests fail",
      "build it",
      "Can you summarise this file?",
    ]) expect(looksLikeIdea(t)).toBe(false);
  });
});

describe("spoken commands", () => {
  it("recognises short commands only", () => {
    expect(voiceCommand("Skip.")).toBe("skip");
    expect(voiceCommand("nova, next question")).toBe("skip");
    expect(voiceCommand("passe")).toBe("skip");
    expect(voiceCommand("Go ahead")).toBe("continue");
    expect(voiceCommand("vas-y")).toBe("continue");
    expect(voiceCommand("stop")).toBe("stop");
    expect(voiceCommand("Yes, that's right.")).toBe("yes");
    expect(voiceCommand("oui c'est ça")).toBe("yes");
    expect(voiceCommand("yes")).toBe("yes");
    expect(voiceCommand("exactement")).toBe("yes");
    expect(voiceCommand("Explain why")).toBe("why");
    expect(voiceCommand("Change the approach")).toBe("change");
    expect(voiceCommand("Make a reasonable assumption")).toBe("assume");
    expect(voiceCommand("Skip the part about pricing because we have not decided it yet")).toBeNull();
    expect(voiceCommand("home gardeners growing tomatoes")).toBeNull();
  });
});
