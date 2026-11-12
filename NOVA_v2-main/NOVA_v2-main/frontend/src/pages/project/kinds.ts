import type { ArtifactKind } from "../../api/types";

export const KIND_LABELS: Record<ArtifactKind, string> = {
  idea_analysis: "Idea analysis",
  requirements: "Requirements",
  user_stories: "User stories",
  architecture: "Architecture",
  api_contract: "API contract",
  data_model: "Data model",
  ui_spec: "UI screen spec",
  component_inventory: "Component inventory",
  implementation_plan: "Implementation plan",
  research_brief: "Research brief",
  review_summary: "Review summary",
  project_summary: "Project summary",
  note: "Note",
  feasibility: "Feasibility analysis",
  technology_choices: "Technology choices",
  risk_register: "Risk register",
  research_plan: "Research plan",
  proposal: "Proposal",
};

export const DESIGN_KINDS: ArtifactKind[] = [
  "feasibility",
  "technology_choices",
  "risk_register",
  "requirements",
  "user_stories",
  "architecture",
  "api_contract",
  "data_model",
  "ui_spec",
  "component_inventory",
  "implementation_plan",
  "review_summary",
];
