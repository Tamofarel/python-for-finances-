"""Process-wide application state (built once per app instance; tests build their own)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from app.core.config import Settings
from app.db.session import Database
from app.providers.registry import ProviderRegistry
from app.research import ResearchProvider
from app.security import RateLimiter
from app.services.agent import ActionRuns
from app.tools.browser import BrowserAdapter
from app.tools.commands import CommandRunner
from app.tools.home_assistant import HomeAssistant
from app.tools.registry import ToolRegistry
from app.tools.spotify import SpotifyService
from app.workflows.runner import WorkflowJobs


@dataclass
class AppState:
    settings: Settings
    db: Database
    providers: ProviderRegistry
    research: ResearchProvider
    runner: CommandRunner
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    spotify: SpotifyService | None = None
    home: HomeAssistant | None = None
    browser: BrowserAdapter | None = None
    action_runs: ActionRuns = field(default_factory=ActionRuns)
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(10, 300))
    setup_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(10, 300))
    workflow_jobs: WorkflowJobs = field(default_factory=WorkflowJobs)
    work_jobs: WorkflowJobs = field(default_factory=WorkflowJobs)
    setup_code: str = field(default_factory=lambda: f"{secrets.randbelow(10**8):08d}")
