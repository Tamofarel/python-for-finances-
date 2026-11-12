"""Pydantic request/response models (validated at the API boundary)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_RE = r"^[A-Za-z0-9_.\-]{3,64}$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ---------------------------------------------------------------------------
class SetupIn(BaseModel):
    username: str = Field(pattern=USERNAME_RE)
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=256)
    setup_code: str = Field(min_length=4, max_length=32)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class UserOut(ORM):
    id: int
    username: str
    display_name: str
    is_owner: bool


class AuthStatus(BaseModel):
    initialized: bool
    authenticated: bool
    user: UserOut | None = None
    csrf_token: str | None = None


# ---- providers ------------------------------------------------------------------------
class ProviderStatusOut(BaseModel):
    name: str
    label: str
    kind: str
    configured: bool
    reachable: bool
    models: list[str]
    default_model: str
    error: str | None
    supports_streaming: bool


class ProviderSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)


class ProvidersOut(BaseModel):
    providers: list[ProviderStatusOut]
    selected: ProviderSelection | None


# ---- conversations ----------------------------------------------------------------------
Mode = Literal["general", "engineering", "research", "product", "design", "business", "learning"]


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    project_id: int | None = None
    mode: Mode = "general"


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: int | None = None
    detach_project: bool = False
    mode: Mode | None = None


class AttachmentOut(ORM):
    id: int
    filename: str
    mime: str
    size: int


class MessageOut(ORM):
    id: int
    role: str
    content: str
    status: str
    error: str | None
    provider: str | None
    model: str | None
    created_at: datetime
    attachments: list[AttachmentOut] = []
    actions: list[dict[str, Any]] | None = None


class ConversationOut(ORM):
    id: int
    title: str
    mode: str = "general"
    project_id: int | None
    provider: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=4)
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)


class RetryIn(BaseModel):
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)


# ---- projects ---------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    instructions: str = Field(default="", max_length=20_000)
    workspace_path: str | None = Field(default=None, max_length=1024)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=20_000)
    archived: bool | None = None


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    instructions: str
    workspace_path: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class DeleteConfirm(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=200)


# ---- files --------------------------------------------------------------------------
class FileEntryOut(BaseModel):
    name: str
    path: str
    type: str
    size: int
    modified: str


class FileContentOut(BaseModel):
    path: str
    content: str
    sha256: str
    size: int


class CreatePathIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    type: Literal["file", "dir"] = "file"
    content: str = Field(default="", max_length=1_000_000)


# ---- change proposals ----------------------------------------------------------------
class ProposeChangeIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=1_000_000)
    summary: str = Field(default="", max_length=2000)
    base_sha256: str | None = Field(default=None, max_length=64)


class AIChangeIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    instruction: str = Field(min_length=3, max_length=10_000)
    provider: str | None = None
    model: str | None = None


class ApplyChangeIn(BaseModel):
    proposed_sha256: str = Field(min_length=64, max_length=64)


class ChangeOut(ORM):
    id: int
    project_id: int
    path: str
    operation: str
    base_sha256: str | None
    proposed_sha256: str
    diff: str
    summary: str
    source: str
    status: str
    created_at: datetime
    decided_at: datetime | None


# ---- actions / tasks ------------------------------------------------------------------
class CommandRequestIn(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=2000)


class DeleteRequestIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    reason: str = Field(default="", max_length=2000)


class MoveRequestIn(BaseModel):
    src: str = Field(min_length=1, max_length=1024)
    dst: str = Field(min_length=1, max_length=1024)
    reason: str = Field(default="", max_length=2000)


class ApproveIn(BaseModel):
    params_hash: str = Field(min_length=64, max_length=64)
    confirm: bool

    @field_validator("confirm")
    @classmethod
    def _must_confirm(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Explicit confirmation is required.")
        return v


class ActionOut(ORM):
    id: int
    project_id: int | None
    kind: str
    category: str
    params: dict[str, Any]
    params_hash: str
    reason: str
    reversible: bool
    status: str
    created_at: datetime
    decided_at: datetime | None


class TaskOut(ORM):
    id: int
    project_id: int | None
    action_id: int | None
    kind: str
    status: str
    command: list[str] | None
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None
    duration_seconds: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ApproveOut(BaseModel):
    action: ActionOut
    task: TaskOut | None = None
    result: dict[str, Any] | None = None


# ---- artifacts ------------------------------------------------------------------------
ArtifactKind = Literal[
    "idea_analysis", "requirements", "user_stories", "architecture", "api_contract", "data_model",
    "ui_spec", "component_inventory", "implementation_plan", "research_brief", "review_summary",
    "project_summary", "note", "feasibility", "technology_choices", "risk_register", "research_plan", "proposal",
]


class ArtifactCreate(BaseModel):
    kind: ArtifactKind = "note"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=500_000)


class ArtifactUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=500_000)


class ArtifactGenerateIn(BaseModel):
    kind: ArtifactKind
    input: str = Field(min_length=3, max_length=50_000)
    title: str | None = Field(default=None, max_length=200)
    provider: str | None = None
    model: str | None = None


class ArtifactExportIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class ArtifactOut(ORM):
    id: int
    project_id: int
    kind: str
    title: str
    content: str
    created_by: str
    source_input: str
    created_at: datetime
    updated_at: datetime


# ---- memory --------------------------------------------------------------------------
class MemoryCreate(BaseModel):
    kind: Literal["preference", "fact", "note"] = "fact"
    content: str = Field(min_length=1, max_length=5000)
    project_id: int | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    kind: Literal["preference", "fact", "note"] | None = None
    enabled: bool | None = None


class MemoryOut(ORM):
    id: int
    kind: str
    content: str
    project_id: int | None
    source: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---- research / audit -----------------------------------------------------------------
class ResearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    synthesize: bool = False
    project_id: int | None = None
    provider: str | None = None
    model: str | None = None


class AuditOut(ORM):
    id: int
    action: str
    target: str
    outcome: str
    details: dict[str, Any]
    ip: str | None
    created_at: datetime


class SummaryIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    focus: str = Field(default="", max_length=2000)


# ---- workflows (0.2) -----------------------------------------------------------------------------------
class WorkflowCreate(BaseModel):
    idea: str = Field(min_length=10, max_length=10_000)
    template: str = Field(default="general", max_length=40)
    title: str | None = Field(default=None, max_length=120)
    project_id: int | None = None


class WorkflowAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=10_000)


class WorkflowMode(BaseModel):
    mode: Literal["auto", "ai", "template"] = "auto"


class WorkflowDecision(BaseModel):
    decision: Literal["approve", "revise", "reject"]
    proposal_sha256: str | None = Field(default=None, max_length=64)
    feedback: str = Field(default="", max_length=5000)
    reopen_stage: str | None = Field(default=None, max_length=24)
    mode: Literal["auto", "ai", "template"] = "auto"


# ---- work items (0.2) ------------------------------------------------------------------------------------
WorkStatus = Literal["proposed", "todo", "in_progress", "awaiting_approval", "blocked", "done", "failed", "cancelled"]


class WorkItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    domain: str = Field(default="general", max_length=40)
    executor: Literal["manual", "command", "artifact"] = "manual"
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list, max_length=50)


class WorkItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    domain: str | None = Field(default=None, max_length=40)
    status: WorkStatus | None = None
    depends_on: list[int] | None = Field(default=None, max_length=50)


class WorkItemOut(ORM):
    id: int
    project_id: int
    workflow_id: int | None
    title: str
    description: str
    domain: str
    executor: str
    status: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    depends_on: list[int]
    requires_approval: bool
    action_id: int | None
    source: str
    error: str | None
    position: int
    created_at: datetime
    updated_at: datetime


class WorkEventOut(ORM):
    id: int
    work_item_id: int | None
    workflow_id: int | None
    kind: str
    message: str
    data: dict[str, Any]
    created_at: datetime
