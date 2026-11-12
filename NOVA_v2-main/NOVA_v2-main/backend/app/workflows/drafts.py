"""Drafts assembled from the owner's own answers when no AI model is available.

These documents contain only what the owner said, the template's authored guidance, and explicit TODOs.
They never contain invented facts, figures or sources.
"""

from __future__ import annotations

from app.models import ResearchFinding, Workflow, WorkflowQuestion
from app.services.artifacts import label_for
from app.workflows.templates import Template

SECTIONS: dict[str, tuple[str, ...]] = {
    "feasibility": ("Summary verdict", "Assumptions", "Technical feasibility", "Data and operational feasibility",
                    "Alternatives considered", "Recommendation"),
    "architecture": ("Components and responsibilities", "Data flow", "Where processing happens", "Key decisions and trade-offs"),
    "data_model": ("Entities", "Fields and constraints", "Relationships", "Retention and privacy"),
    "api_contract": ("Endpoints", "Request and response examples", "Errors", "Authentication"),
    "ui_spec": ("Screen inventory", "Primary user journey", "States (empty, loading, error, uncertain)", "Accessibility"),
    "technology_choices": ("Decisions to make", "Options compared", "Recommendation"),
    "requirements": ("Goals", "Users", "Scope", "Functional requirements", "Non-functional requirements", "Out of scope"),
    "user_stories": ("Epics", "Stories with acceptance criteria"),
    "implementation_plan": ("Phases", "Ordered tasks", "Dependencies", "Definition of done"),
    "risk_register": ("Risk table (ID, risk, likelihood, impact, mitigation, owner)",),
}

DISCLAIMER = ("> **Draft assembled from your answers.** No AI model was used for this document. It contains only "
              "your decisions, the template's guidance and the sections still to write.")


def _qa_lines(questions: list[WorkflowQuestion], topic_ids: set[str] | None) -> list[str]:
    out = []
    for q in questions:
        if q.status != "answered" or (topic_ids is not None and q.topic not in topic_ids):
            continue
        out.append(f"- **{q.question}**  \n  {q.answer}")
    return out


def assemble(kind: str, wf: Workflow, tpl: Template, questions: list[WorkflowQuestion],
             findings: list[ResearchFinding]) -> str:
    feeding = {t.id for t in tpl.topics if kind in t.feeds}
    lines = [f"# {label_for(kind)} — {wf.title}", "", DISCLAIMER, "", "## Idea", "", wf.idea.strip(), ""]
    relevant = _qa_lines(questions, feeding)
    lines += ["## Decisions you made that shape this document", ""]
    lines += relevant or ["- None yet. Answer the related questions in the workflow, then regenerate."]
    lines.append("")
    guidance = list(tpl.guidance.get("*", ())) + list(tpl.guidance.get(kind, ()))
    if guidance:
        lines += ["## Guidance this document must follow", ""] + [f"- {g}" for g in guidance] + [""]
    if kind in ("feasibility", "risk_register", "technology_choices"):
        pending = [f for f in findings if f.status != "sourced"]
        if pending:
            lines += ["## Research still pending", ""] + [f"- {f.question}" for f in pending] + [""]
    open_topics = [t for t in tpl.topics if kind in t.feeds and not any(
        q.topic == t.id and q.status == "answered" for q in questions)]
    if open_topics:
        lines += ["## Open questions", ""] + [f"- {t.question}" for t in open_topics] + [""]
    for section in SECTIONS.get(kind, ()):
        lines += [f"## {section}", "", "_TODO: to be written._", ""]
    notes = [n["feedback"] for n in (wf.revision_notes or []) if isinstance(n, dict) and n.get("feedback")]
    if notes:
        lines += ["## Revision requests to address", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines).rstrip() + "\n"


def research_plan(wf: Workflow, findings: list[ResearchFinding], reason: str) -> str:
    lines = [f"# Research plan — {wf.title}", "",
             f"> **Research is pending.** {reason} No search results were fabricated; every item below still needs "
             "to be investigated.", "", "| # | Question | Where to look | Status |", "|---|---|---|---|"]
    for i, f in enumerate(findings, 1):
        cats = "; ".join(f.source_categories) or "—"
        lines.append(f"| {i} | {f.question} | {cats} | {f.status} |")
    sourced = [f for f in findings if f.status == "sourced"]
    if sourced:
        lines += ["", "## Sources returned by search (not yet synthesised)", ""]
        for f in sourced:
            lines.append(f"**{f.question}**")
            for r in f.results:
                lines.append(f"- [{r.get('title') or r.get('url')}]({r.get('url')}) — {r.get('snippet', '')[:200]}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
