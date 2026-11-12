"""Provider registry: distinguishes configured, reachable, available models, selection and failures."""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import AppError, NotFound
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import AIProvider, ProviderStatus
from app.providers.ollama import OllamaProvider


class ProviderRegistry:
    def __init__(self, providers: list[AIProvider] | None = None) -> None:
        self._providers: dict[str, AIProvider] = {}
        for p in providers or []:
            self.register(p)

    def register(self, provider: AIProvider) -> None:
        self._providers[provider.name] = provider

    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> AIProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise NotFound(f"Unknown provider '{name}'.", code="unknown_provider") from exc

    def require_configured(self, name: str) -> AIProvider:
        p = self.get(name)
        if not p.is_configured():
            raise AppError(f"Provider '{p.label}' is not configured.", code="provider_not_configured")
        return p

    async def statuses(self) -> list[ProviderStatus]:
        return [await p.status() for p in self._providers.values()]


def build_registry(settings: Settings) -> ProviderRegistry:
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return ProviderRegistry([
        OllamaProvider(settings.ollama_base_url, settings.ollama_model, settings.ollama_timeout_seconds,
                       settings.ollama_keep_alive),
        AnthropicProvider(key, settings.anthropic_model, settings.anthropic_max_tokens,
                          settings.anthropic_timeout_seconds),
    ])
