"""Honest capability reporting.

Each capability says what it is, how it is implemented, what it needs, and whether those requirements are met right
now. Statuses come from live checks (provider reachability, model vision support, search provider, local speech
engines) — never from configuration alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.state import AppState
from app.models import User
from app.services.agent import is_paused
from app.services.assistant import SELECTED_PROVIDER_KEY, get_setting
from app.voice import engines


@dataclass
class Capability:
    id: str
    label: str
    description: str
    status: str  # available | limited | unavailable
    reason: str | None
    implementation: str
    entry: str  # where to use it in the app
    requires: list[str] = field(default_factory=list)


async def compute(db: Session, user: User, state: AppState) -> list[dict[str, Any]]:
    statuses = {s.name: s for s in await state.providers.statuses()}
    sel = get_setting(db, user, SELECTED_PROVIDER_KEY)
    provider_name = sel.get("provider") if isinstance(sel, dict) else state.settings.default_provider
    if provider_name not in statuses:
        provider_name = state.settings.default_provider
    model = (sel.get("model") if isinstance(sel, dict) else None) or (
        state.providers.get(provider_name).default_model() if provider_name in statuses else "")
    st = statuses.get(provider_name)
    ai_ok = bool(st and st.reachable and not st.error)
    ai_reason = None if ai_ok else (st.error if st else "No AI provider selected.") or "The selected model is not available."
    any_ai = any(s.reachable and not s.error for s in statuses.values())

    vision_status, vision_reason = "unavailable", ai_reason
    if ai_ok and st is not None:
        supports = await state.providers.get(provider_name).supports_images(model)
        if supports is True:
            vision_status, vision_reason = "available", None
        elif supports is False:
            vision_reason = (f"The selected model '{model}' does not read images. Choose a vision model "
                             "(for example llama3.2-vision or qwen2.5vl in Ollama, or a Claude model).")
        else:
            vision_status, vision_reason = "limited", (f"{st.label} does not report whether '{model}' reads images; "
                                                       "NOVA will try and show the provider's answer or error.")

    enabled_tools = state.tools.enabled_tools()
    if not enabled_tools:
        actions_status, actions_reason = "unavailable", " ".join(sorted({
            t.unavailable_reason for t in state.tools.all() if t.unavailable_reason})) or "No action integrations."
    elif not ai_ok:
        actions_status, actions_reason = "unavailable", ai_reason
    else:
        tools_ok = await state.providers.get(provider_name).supports_tools(model)
        if tools_ok is False:
            actions_status, actions_reason = "unavailable", (
                f"The selected model '{model}' cannot call tools. Choose one that can (llama3.2 or mistral in Ollama, "
                "or a Claude model).")
        elif tools_ok is None:
            actions_status, actions_reason = "limited", f"{st.label if st else 'The provider'} does not report whether " \
                f"'{model}' can call tools; NOVA will try and report what happens."
        else:
            actions_status, actions_reason = "available", None
        if is_paused(db, user):
            actions_status, actions_reason = "limited", "Paused by the emergency stop. Resume in Settings → Actions."
    actions_impl = "Model-driven tool calls through a typed registry: " + ", ".join(t.name for t in enabled_tools) \
        if enabled_tools else "Model-driven tool calls through a typed registry (no tools enabled)."

    research_err = await state.research.check()
    wake = engines.wake_status()
    stt = {e.id: e for e in engines.engine_statuses(state.settings.vosk_model_path)}
    local_stt = [e.label for e in stt.values() if e.local and e.available]

    def ai_dependent(cap_id: str, label: str, desc: str, impl: str, entry: str, offline_ok: str | None = None) -> Capability:
        if ai_ok:
            return Capability(cap_id, label, desc, "available", None, impl, entry, ["AI model"])
        if offline_ok:
            return Capability(cap_id, label, desc, "limited", f"{ai_reason} {offline_ok}", impl, entry, ["AI model (optional)"])
        return Capability(cap_id, label, desc, "unavailable", ai_reason, impl, entry, ["AI model"])

    caps = [
        ai_dependent("conversation", "Conversation", "Streaming conversations with persistent, searchable history.",
                     "Provider interface (Ollama, Anthropic) with SSE streaming.", "/chat"),
        ai_dependent("software_engineering", "Software engineering and debugging",
                     "Explore project files, propose changes as reviewable diffs, run allowlisted tests after approval.",
                     "Confined workspaces, change proposals, approval-gated command runner, engineering mode.",
                     "/projects", "Files, diffs and approved commands still work without a model."),
        Capability("research", "Research with sources",
                   "Web search with source URLs and briefs that separate facts from assumptions.",
                   "available" if research_err is None else "unavailable", research_err,
                   "SearXNG search provider; results stored with URLs; nothing is invented when search fails.",
                   "/workflows", ["Search provider (SearXNG)"]),
        ai_dependent("product_discovery", "Product discovery and architecture",
                     "Idea-to-product workflows: one question at a time, feasibility, architecture, proposal, tasks.",
                     "Workflow engine with templates, editable documents and approval-bound proposals.", "/workflows",
                     "Without a model, documents are assembled from your answers."),
        ai_dependent("ui_ux", "UI/UX and visual design",
                     "User journeys, screen inventories, interaction flows and component specs as editable documents.",
                     "Workflow UX stage and design documents (Markdown and Mermaid). No image or Figma generation.",
                     "/workflows", "Without a model, UX documents are scaffolds of your answers."),
        Capability("vision", "Image and screenshot understanding",
                   "Attach a photo or screenshot to a message and ask about it.", vision_status, vision_reason,
                   "Image attachments sent to vision-capable models (Claude, or Ollama vision models).",
                   "/chat", ["Vision-capable AI model"]),
        ai_dependent("business_marketing", "Business strategy and marketing",
                     "Business and marketing workflows and a business mode for conversations.",
                     "Workflow templates (business idea, marketing campaign) and a business mode preset.", "/workflows",
                     "Workflows still collect your decisions offline."),
        ai_dependent("learning", "Learning and explanations", "Step-by-step explanations with checks for understanding.",
                     "Learning mode preset in conversations.", "/chat"),
        Capability("files", "File and project organization",
                   "Browse, create, edit (with diffs), rename or move and delete files inside project workspaces.",
                   "available", None,
                   "Confined workspaces; edits as proposals; move/rename and delete need your approval.",
                   "/projects", []),
        Capability("automation", "Task tracking and authorized automation",
                   "Tasks with dependencies; allowlisted commands run only after you approve the exact command.",
                   "available", None,
                   "Task engine + approval-gated, shell-free command runner. No deployment, payments or messages to "
                   "third parties.", "/projects", ["Command allowlist"]),
        Capability("actions", "Actions on this computer and devices",
                   "Ask in words to play music on Spotify, open approved apps, websites and project files, or control "
                   "smart-home devices. Results are verified where the integration allows; safety-critical actions "
                   "wait for your approval.", actions_status, actions_reason, actions_impl, "/settings?tab=actions",
                   ["NOVA_DESKTOP_CONTROL_ENABLED or Home Assistant", "A model that can call tools"]),
        Capability("voice", "Voice conversation",
                   "Push-to-talk, wake phrase, spoken replies and interruption.",
                   "available" if wake.available else "limited",
                   None if wake.available else wake.reason,
                   "Local wake phrase (PocketSphinx); speech-to-text: browser"
                   + (", " + ", ".join(local_stt) if local_stt else "") + "; speech output: browser voices.",
                   "/settings?tab=voice", ["Microphone permission"]),
    ]
    if not any_ai:
        for c in caps:
            if c.id == "conversation":
                c.reason = (c.reason or "") + " Configure Ollama or Anthropic in Models."
    return [asdict(c) for c in caps]
