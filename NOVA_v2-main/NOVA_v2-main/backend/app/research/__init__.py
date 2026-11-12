"""Research engine interface, kept separate from chat so more search providers can be added.

Only real providers are implemented. When none is configured, research is reported as unavailable;
NOVA never fabricates search results or citations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

import httpx


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str  # engine/provider that returned it

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ResearchUnavailable(Exception):
    pass


class ResearchProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...

    async def check(self) -> str | None:
        """Live reachability check. Returns an error message, or None when the provider answered."""
        if not self.available():
            return "No search provider is configured."
        try:
            await self.search("nova", limit=1)
        except ResearchUnavailable as exc:
            return str(exc)
        return None


class DisabledResearch(ResearchProvider):
    name = "none"

    def available(self) -> bool:
        return False

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        raise ResearchUnavailable(
            "Web research is not configured. Set NOVA_RESEARCH_PROVIDER=searxng and NOVA_SEARXNG_URL "
            "to a SearXNG instance you control (JSON format enabled).")


class SearxngResearch(ResearchProvider):
    """Queries a SearXNG instance (self-hosted metasearch) via its JSON API."""

    name = "searxng"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def available(self) -> bool:
        return bool(self.base_url)

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
                resp = await client.get(f"{self.base_url}/search", params={"q": query, "format": "json"})
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchUnavailable(f"Search provider failed: {type(exc).__name__}") from exc
        out: list[SearchResult] = []
        for r in data.get("results", [])[:limit]:
            url = str(r.get("url", ""))
            if not url.startswith(("http://", "https://")):
                continue
            out.append(SearchResult(title=str(r.get("title", ""))[:300], url=url,
                                    snippet=str(r.get("content", ""))[:600],
                                    source=str(r.get("engine", "searxng"))))
        return out


def build_research(provider: str, searxng_url: str) -> ResearchProvider:
    if provider == "searxng" and searxng_url:
        return SearxngResearch(searxng_url)
    return DisabledResearch()
