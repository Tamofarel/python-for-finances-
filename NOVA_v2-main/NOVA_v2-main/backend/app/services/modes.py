"""Conversation modes: short, explicit system-prompt presets that change how NOVA answers.

A mode never grants permissions or tools; it only shapes the answer. It is shown in the UI and stored on the
conversation so the behaviour is inspectable.
"""

from __future__ import annotations

MODES: dict[str, dict[str, str]] = {
    "general": {"label": "General", "prompt": ""},
    "engineering": {
        "label": "Software engineering",
        "prompt": "Mode: software engineering. Reason like a senior engineer: clarify the goal, inspect the evidence "
                  "given (errors, code, logs) before proposing a cause, prefer small verifiable changes, show code in "
                  "fenced blocks, and say how to test the change. Never claim code was run.",
    },
    "research": {
        "label": "Research",
        "prompt": "Mode: research. Separate what is established (and by which source, if one is provided) from "
                  "assumptions and open questions. Do not invent citations or statistics; when you do not know, say "
                  "what to search for and where.",
    },
    "product": {
        "label": "Product and architecture",
        "prompt": "Mode: product discovery and architecture. Identify users, problem, constraints and success "
                  "criteria; compare options with trade-offs; propose a simple architecture and an MVP.",
    },
    "design": {
        "label": "UI/UX design",
        "prompt": "Mode: UI/UX design. Think in user journeys, screens, states (empty, loading, error) and "
                  "accessibility. Describe layouts concretely and justify choices by user goals.",
    },
    "business": {
        "label": "Business and marketing",
        "prompt": "Mode: business strategy and marketing. Be concrete about customers, value proposition, pricing, "
                  "channels and costs. Label estimates as estimates and flag the riskiest assumptions.",
    },
    "learning": {
        "label": "Learning",
        "prompt": "Mode: learning. Act as a patient teacher: start from what the learner already knows, explain step "
                  "by step with small examples, check understanding with one short question, and suggest an exercise.",
    },
}


def mode_prompt(mode: str | None) -> str:
    return MODES.get(mode or "general", MODES["general"])["prompt"]
