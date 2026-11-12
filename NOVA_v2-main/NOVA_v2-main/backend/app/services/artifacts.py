"""Prompt templates for generated artifacts (idea analysis and design documents)."""

from __future__ import annotations

ARTIFACT_LABELS: dict[str, str] = {
    "idea_analysis": "Idea analysis",
    "requirements": "Requirements document",
    "user_stories": "User stories",
    "architecture": "Architecture",
    "api_contract": "API contract",
    "data_model": "Data model",
    "ui_spec": "UI screen specification",
    "component_inventory": "Component inventory",
    "implementation_plan": "Implementation roadmap",
    "research_brief": "Research brief",
    "review_summary": "Review summary",
    "project_summary": "Project summary",
    "note": "Note",
    "feasibility": "Feasibility analysis",
    "technology_choices": "Technology and deployment choices",
    "risk_register": "Risk register",
    "research_plan": "Research plan",
    "proposal": "Project brief and proposal",
}

_INSTRUCTIONS: dict[str, str] = {
    "idea_analysis": """Analyse the idea below. Produce Markdown with exactly these sections:
## Problem statement
## Target users and needs
## Assumptions and uncertainties (mark each as Assumption / Uncertainty)
## Feasibility
## Risks (technical and non-technical, with likelihood and mitigation)
## Alternative approaches
## Recommended solution and justification
## Proposed architecture (include a ```mermaid diagram if useful)
## Milestones and acceptance criteria
## Questions for the owner
Challenge weak assumptions honestly; do not simply agree with the idea.""",
    "requirements": "Write a requirements document in Markdown: scope, functional requirements (numbered, "
                    "testable), non-functional requirements, constraints, out of scope, open questions.",
    "user_stories": "Write user stories in Markdown grouped by epic. Each: 'As a … I want … so that …' plus "
                    "acceptance criteria in Given/When/Then form.",
    "architecture": "Describe a software architecture in Markdown: components and responsibilities, data flow, "
                    "key decisions with trade-offs, and at least one ```mermaid diagram (flowchart or C4-like).",
    "api_contract": "Write an API contract in Markdown: endpoints table (method, path, purpose, auth), request "
                    "and response JSON examples, error format, versioning notes.",
    "data_model": "Write a data model in Markdown: entities, fields with types and constraints, relationships, "
                  "indexes, and a ```mermaid erDiagram.",
    "ui_spec": "Write UI screen specifications in Markdown: for each screen, purpose, layout regions, components, "
               "states (empty/loading/error), interactions and accessibility notes.",
    "component_inventory": "Write a UI component inventory in Markdown: a table of components with purpose, "
                           "props/variants, states and where they are used.",
    "implementation_plan": "Write an implementation plan in Markdown: phases, ordered tasks with estimates, "
                           "dependencies, risks, and a definition of done per phase.",
    "review_summary": "Write a review summary in Markdown: what changed, risks, test evidence present in the "
                      "input (do not invent results), and follow-ups.",
    "research_brief": "Write a research brief in Markdown separating Source-backed facts, Inferences, "
                      "Unverified statements and Conflicts.",
    "project_summary": "Summarise the project in Markdown.",
    "note": "Write a clear, well-structured Markdown note.",
    "feasibility": "Write a feasibility analysis in Markdown with sections: Summary verdict, Users and value, "
                   "Assumptions (each marked Assumption), Technical feasibility, Data/operational feasibility, Privacy, "
                   "Cost (mark numbers as Estimate with their basis), Risks, Alternatives considered (table with "
                   "pros/cons), Recommendation, Open questions.",
    "technology_choices": "Compare technology and deployment options in Markdown: for each decision (e.g. platform, "
                          "framework, hosting/inference, storage) give a comparison table (option, pros, cons, cost, fit) "
                          "and a recommendation with its reason.",
    "risk_register": "Write a risk register in Markdown as a table: ID, risk, category, likelihood (L/M/H), impact "
                     "(L/M/H), mitigation, owner, status. Follow with the top 3 risks explained.",
    "research_plan": "Write a research plan in Markdown: each open question, why it matters, and which source "
                     "categories to check.",
    "proposal": "Write a consolidated proposal in Markdown.",
}


def instruction_for(kind: str) -> str:
    return _INSTRUCTIONS.get(kind, _INSTRUCTIONS["note"])


def label_for(kind: str) -> str:
    return ARTIFACT_LABELS.get(kind, kind)
