"""Application configuration, loaded from environment variables (prefix NOVA_) and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_ALLOWLIST = (
    "python -m pytest;pytest;python -m unittest;npm test;npm run test;npm run build;"
    "npm run lint;git status;git diff;git log;ruff check"
)
DEFAULT_BROWSER_HOSTS = (
    "localhost,127.0.0.1,google.com,youtube.com,github.com,stackoverflow.com,wikipedia.org,"
    "developer.mozilla.org,docs.python.org,open.spotify.com"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVA_",
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server / network exposure -------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    environment: str = "development"  # development | production | test
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cookie_secure: bool = False  # set true when served over HTTPS
    session_ttl_hours: int = 72
    max_request_bytes: int = 2_000_000

    # --- Storage ------------------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str | None = None  # defaults to sqlite in data_dir
    allowed_workspace_roots: str = ""  # ';'-separated extra roots allowed for project workspaces
    max_file_bytes: int = 1_000_000

    # --- AI providers ---------------------------------------------------------------
    default_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 120.0
    ollama_keep_alive: str = "10m"
    anthropic_api_key: SecretStr | None = Field(default=None)
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_max_tokens: int = 4096
    anthropic_timeout_seconds: float = 120.0

    # --- Research -----------------------------------------------------------------
    research_provider: str = "none"  # none | searxng
    searxng_url: str = ""

    # --- Voice (0.3) ----------------------------------------------------------------
    voice_enabled: bool = True
    # Microphone audio for wake-word detection and local transcription is only accepted from this computer
    # (loopback). Set true only if you serve NOVA over HTTPS on a network you trust.
    voice_allow_remote: bool = False
    vosk_model_path: str = ""  # folder of an unpacked Vosk model, enables the optional Vosk engine
    # Optional language-to-folder map, separated by |: en-US=C:\\models\\vosk-en|fr-FR=C:\\models\\vosk-fr.
    vosk_model_paths: str = ""
    voice_max_utterance_seconds: int = 20

    # --- Attachments (0.3) ---------------------------------------------------------------
    max_image_bytes: int = 5_000_000

    # --- Tool execution -------------------------------------------------------------
    command_allowlist: str = DEFAULT_ALLOWLIST
    command_timeout_seconds: int = 300
    command_max_output_bytes: int = 200_000
    # Separate opt-in for desktop actions; never inherits command allowlist permissions.
    desktop_control_enabled: bool = False
    spotify_client_id: str = ""
    # Extra approved apps: "name=C:\\path\\App.exe|app.exe|alias1,alias2;name=uri-scheme:|process.exe".
    desktop_apps: str = ""
    # Websites open_website may open (a host also allows its subdomains). Comma-separated.
    browser_allowed_hosts: str = DEFAULT_BROWSER_HOSTS

    # --- Smart home (Home Assistant) -------------------------------------------------------------------------
    home_assistant_url: str = ""  # e.g. http://homeassistant.local:8123
    home_assistant_token: SecretStr | None = Field(default=None)  # long-lived access token
    # Devices NOVA may use: "light.kitchen, switch.desk_lamp=reversible, lock.front_door". Nothing else is reachable.
    home_assistant_entities: str = ""

    # --- Logging ------------------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("anthropic_api_key", "home_assistant_token", mode="before")
    @classmethod
    def _empty_key_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'nova.db').as_posix()}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def default_workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def workspace_roots(self) -> list[Path]:
        roots = [self.default_workspaces_dir.resolve()]
        for raw in self.allowed_workspace_roots.split(";"):
            raw = raw.strip()
            if raw:
                roots.append(Path(raw).expanduser().resolve())
        return roots

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowlisted_commands(self) -> list[list[str]]:
        out: list[list[str]] = []
        for entry in self.command_allowlist.split(";"):
            parts = entry.strip().split()
            if parts:
                out.append(parts)
        return out

    def vosk_model_for(self, language: str) -> str:
        models: dict[str, str] = {}
        for entry in self.vosk_model_paths.split("|"):
            lang, sep, path = entry.partition("=")
            if sep and lang.strip() and path.strip():
                models[lang.strip().casefold()] = path.strip()
        exact = models.get(language.casefold())
        if exact:
            return exact
        base = language.split("-", 1)[0].casefold()
        return next((path for lang, path in models.items() if lang.split("-", 1)[0] == base), self.vosk_model_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
