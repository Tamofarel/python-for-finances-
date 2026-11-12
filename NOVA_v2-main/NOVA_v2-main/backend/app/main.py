"""NOVA FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update

from app.api import actions, artifacts, auth, conversations, files, misc, projects, providers, tools, voice, work, workflows
from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.state import AppState
from app.db.migrate import upgrade
from app.db.session import Database
from app.models import Message, Task, Workflow, utcnow
from app.providers.registry import ProviderRegistry, build_registry
from app.research import ResearchProvider, build_research
from app.services.auth import user_count
from app.tools.browser import BrowserAdapter
from app.tools.commands import CommandRunner
from app.tools.home_assistant import HomeAssistant, home_tools, parse_entities
from app.tools.registry import ToolRegistry
from app.tools.spotify import SpotifyService
from app.tools.windows_agent import DEFAULT_APPS, desktop_tools, parse_apps

log = get_logger("nova")

CLIENT_HEADER = "X-NOVA-Client"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    research: ResearchProvider | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.default_workspaces_dir.mkdir(parents=True, exist_ok=True)

    upgrade(settings.resolved_database_url)  # explicit, versioned migrations
    db = Database(settings.resolved_database_url)
    spotify_service = SpotifyService(settings.spotify_client_id, settings.port, settings.data_dir / "spotify-token.bin")
    home = HomeAssistant(settings.home_assistant_url,
                         settings.home_assistant_token.get_secret_value() if settings.home_assistant_token else "",
                         parse_entities(settings.home_assistant_entities))
    browser = BrowserAdapter(settings.browser_allowed_hosts)
    tool_registry = ToolRegistry(
        desktop_tools(settings.desktop_control_enabled, spotify_service,
                      apps={**DEFAULT_APPS, **parse_apps(settings.desktop_apps)},
                      browser_hosts=settings.browser_allowed_hosts,
                      browser=browser if browser.available else None,
                      allowlisted_commands=settings.allowlisted_commands)
        + home_tools(home)
    )
    state = AppState(
        settings=settings,
        db=db,
        providers=registry or build_registry(settings),
        research=research or build_research(settings.research_provider, settings.searxng_url),
        runner=CommandRunner(settings.command_timeout_seconds, settings.command_max_output_bytes),
        tools=tool_registry,
        spotify=spotify_service,
        home=home,
        browser=browser,
    )

    _recover_interrupted_work(db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Graceful shutdown: stop running commands so no child process outlives NOVA.
        stopped = (state.runner.cancel_all() + state.workflow_jobs.cancel_all() + state.work_jobs.cancel_all()
                   + state.action_runs.cancel_all())
        if stopped:
            log.info("Stopped %d running command(s) during shutdown.", stopped)
        if state.spotify:
            state.spotify.close()
        if state.home:
            state.home.close()
        if state.browser:
            state.browser.close()
        db.engine.dispose()

    app = FastAPI(
        title="NOVA", version="0.4.1", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None, lifespan=lifespan
    )
    app.state.nova = state
    install_error_handlers(app)

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # A custom header cannot be set cross-site without a CORS preflight, which we restrict.
            if request.headers.get(CLIENT_HEADER) != "web":
                return JSONResponse(
                    {"error": {"code": "missing_client_header", "message": f"Header {CLIENT_HEADER}: web is required."}},
                    status_code=403,
                )
            length = request.headers.get("content-length")
            limit = settings.max_image_bytes + 65_536 if path.endswith("/attachments") else settings.max_request_bytes
            if length and length.isdigit() and int(length) > limit:
                return JSONResponse({"error": {"code": "payload_too_large", "message": "Request too large."}}, status_code=413)
        response: Response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            # Swagger UI (/api/docs) loads its assets from cdn.jsdelivr.net, so it gets no CSP.
            if k == "Content-Security-Policy" and path == "/api/docs":
                continue
            response.headers.setdefault(k, v)
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", CLIENT_HEADER],
    )

    for r in (
        misc.router,
        auth.router,
        providers.router,
        tools.router,
        conversations.router,
        projects.router,
        files.router,
        artifacts.router,
        actions.router,
        workflows.router,
        work.router,
        voice.router,
        conversations.attachments_router,
    ):
        app.include_router(r)

    _mount_frontend(app, frontend_dist or PROJECT_ROOT / "frontend" / "dist")

    with db.SessionLocal() as s:
        if user_count(s) == 0:
            banner = "=" * 64
            print(
                f"\n{banner}\n  NOVA first-run setup code: {state.setup_code}\n"
                f"  Enter it on the setup screen to create the owner account.\n{banner}\n",
                flush=True,
            )
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "NOVA is bound to %s: it is reachable from your network. Use HTTPS and set NOVA_COOKIE_SECURE=true.", settings.host
        )
    return app


def _recover_interrupted_work(db: Database) -> None:
    """Anything still 'running' at startup was interrupted by a crash or restart: say so honestly."""
    with db.SessionLocal() as s:
        s.execute(
            update(Task)
            .where(Task.status.in_(["queued", "running"]))
            .values(status="failed", error="Interrupted: the NOVA backend stopped while this was running.", finished_at=utcnow())
        )
        s.execute(
            update(Message)
            .where(Message.status == "streaming")
            .values(status="error", error="Interrupted: the NOVA backend stopped during generation.")
        )
        s.commit()
        for wf in s.scalars(select(Workflow)):
            stages = dict(wf.stage_state or {})
            changed = False
            for name, st in stages.items():
                if isinstance(st, dict) and st.get("status") == "running" and name not in ("clarify", "decision"):
                    stages[name] = {**st, "status": "interrupted", "error": "The NOVA backend stopped while this stage ran."}
                    changed = True
            if changed:
                wf.stage_state = stages
        s.commit()
        from app.services.work import recover_interrupted

        recover_interrupted(s)


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the production frontend build from the same origin when it exists."""
    index = dist / "index.html"
    if not index.is_file():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": {"code": "not_found", "message": "Not Found"}}, status_code=404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":
    run()
