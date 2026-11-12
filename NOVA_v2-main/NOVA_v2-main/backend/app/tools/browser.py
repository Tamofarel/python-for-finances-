"""Visible, isolated Playwright browser adapter with host and address checks on every request."""

from __future__ import annotations

import importlib.util
import ipaddress
import queue
import re
import socket
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.tools.registry import ToolCancelled, ToolContext, ToolDeadline

MAX_PAGE_TEXT = 8_000
MAX_SEARCH_RESULTS = 12
_STOP = object()


class BrowserUnavailable(Exception):
    """Playwright or its browser executable is not installed/available."""


class BrowserPolicyError(Exception):
    """The requested URL is outside the browser's network policy."""


def validate_url(url: str, hosts: tuple[str, ...], resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    """Canonicalize a URL and reject unapproved hosts and non-public destinations."""
    if len(url) > 2048:
        raise BrowserPolicyError("That address is too long.")
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError) as exc:
        raise BrowserPolicyError("That address is malformed.") from exc
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        raise BrowserPolicyError("Browser navigation requires an approved HTTPS address without embedded credentials.")
    if any(re.search(r"token|secret|password|credential|session|authorization|auth|api.?key|^code$", key, re.I)
           for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise BrowserPolicyError("Addresses with credential-like query parameters are not opened.")
    if port not in (None, 443):
        raise BrowserPolicyError("Only the standard HTTPS port is allowed.")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in hosts):
        raise BrowserPolicyError(f"{host} is not on NOVA's approved website list.")
    try:
        addresses = {ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in resolver(host, 443, type=socket.SOCK_STREAM)}
    except (OSError, ValueError, IndexError) as exc:
        raise BrowserPolicyError("NOVA could not verify that website's network address is public.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise BrowserPolicyError("NOVA blocks websites that resolve to private or special-use network addresses.")
    return parts._replace(scheme="https", netloc=host).geturl()


@dataclass
class _Command:
    owner_id: int
    operation: str
    args: tuple[Any, ...]
    ctx: ToolContext
    future: Future[tuple[str, str, dict[str, Any]]]


class BrowserAdapter:
    """Own Playwright on one dedicated thread so browser objects never cross worker threads."""

    def __init__(self, allowed_hosts: str, *, resolver: Callable[..., Any] = socket.getaddrinfo,
                 playwright_factory: Callable[[], Any] | None = None) -> None:
        self.hosts = tuple(h.strip().lower().strip(".") for h in allowed_hosts.split(",") if h.strip())
        self.resolver = resolver
        self.playwright_factory = playwright_factory
        self._queue: queue.Queue[_Command | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def available(self) -> bool:
        return self.playwright_factory is not None or importlib.util.find_spec("playwright") is not None

    def open(self, owner_id: int, url: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return self._call(owner_id, "open", (url,), ctx)

    def inspect(self, owner_id: int, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return self._call(owner_id, "inspect", (), ctx)

    def search(self, owner_id: int, query: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return self._call(owner_id, "search", (query,), ctx)

    def click_link(self, owner_id: int, text: str, ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        return self._call(owner_id, "click", (text,), ctx)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is not None:
                self._queue.put(_STOP)
        if thread is not None:
            thread.join(timeout=5)

    def _call(self, owner_id: int, operation: str, args: tuple[Any, ...], ctx: ToolContext) -> tuple[str, str, dict[str, Any]]:
        ctx.check()
        future: Future[tuple[str, str, dict[str, Any]]] = Future()
        with self._lock:
            if self._closed:
                return "unavailable", "The NOVA browser adapter is closed.", {"verified": False}
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="nova-browser", daemon=True)
                self._thread.start()
            self._queue.put(_Command(owner_id, operation, args, ctx, future))
        while True:
            try:
                return future.result(timeout=min(0.1, max(0.01, ctx.remaining())))
            except TimeoutError:
                if ctx.cancelled:
                    return "cancelled", ("Stop was requested. The browser may already have reached the site; "
                                          "the final outcome is unknown."), {"verified": False}
                if ctx.remaining() <= 0:
                    ctx.cancel()
                    return "timed_out", "The browser operation timed out; the final outcome is unknown.", {"verified": False}

    def _run(self) -> None:
        playwright = browser = None
        contexts: dict[int, Any] = {}
        pages: dict[int, Any] = {}
        try:
            try:
                factory = self.playwright_factory
                if factory is None:
                    from playwright.sync_api import sync_playwright

                    factory = sync_playwright
                playwright = factory().start()
                browser = playwright.chromium.launch(headless=False)
            except Exception as exc:
                while True:
                    item = self._queue.get()
                    if item is _STOP:
                        return
                    assert isinstance(item, _Command)
                    message = "Install Playwright and Chromium to use browser automation."
                    item.future.set_result(("unavailable", message,
                                            {"verified": False, "reason": type(exc).__name__}))
            while True:
                item = self._queue.get()
                if item is _STOP:
                    break
                assert isinstance(item, _Command)
                if item.future.cancelled():
                    continue
                try:
                    item.ctx.check()
                    result = self._operate(item, browser, contexts, pages)
                except BrowserPolicyError as exc:
                    result = ("denied", str(exc), {"verified": False, "reason": "url_policy"})
                except ToolCancelled:
                    result = ("cancelled", "Stopped before the browser operation began.", {"verified": False})
                except ToolDeadline:
                    result = ("timed_out", "The browser operation ran out of time before it began.", {"verified": False})
                except Exception as exc:
                    result = ("failed", "The isolated browser operation failed.",
                              {"verified": False, "reason": type(exc).__name__})
                if not item.future.done():
                    item.future.set_result(result)
        finally:
            for context in contexts.values():
                with suppress(Exception):
                    context.close()
            if browser is not None:
                with suppress(Exception):
                    browser.close()
            if playwright is not None:
                with suppress(Exception):
                    playwright.stop()


    def _operate(self, item: _Command, browser: Any, contexts: dict[int, Any],
                 pages: dict[int, Any]) -> tuple[str, str, dict[str, Any]]:
        owner = item.owner_id
        if owner not in contexts:
            context = browser.new_context(accept_downloads=False, service_workers="block")

            def guard(route: Any, request: Any) -> None:
                try:
                    validate_url(request.url, self.hosts, self.resolver)
                except BrowserPolicyError:
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", guard)
            if hasattr(context, "route_web_socket"):
                context.route_web_socket("**/*", lambda websocket: websocket.close())
            contexts[owner] = context
            pages[owner] = context.new_page()
        page = pages[owner]
        page.set_default_navigation_timeout(max(1, int(item.ctx.remaining() * 1000)))
        page.set_default_timeout(max(1, int(item.ctx.remaining() * 1000)))
        if item.operation == "open":
            url = validate_url(item.args[0], self.hosts, self.resolver)
            response = page.goto(url, wait_until="domcontentloaded")
            item.ctx.check()
            if response and not 200 <= response.status < 300:
                return "failed", "The page did not finish with a successful response; NOVA did not verify it opened.", {
                    "verified": False, "url": url, "http_status": response.status,
                }
            final = validate_url(page.url, self.hosts, self.resolver)
            return "succeeded", "Opened the approved page in NOVA's isolated browser.", {
                "verified": True, "url": final, "title": page.title()[:500], "text": _visible_text(page),
                "http_status": response.status if response else None,
            }
        if owner not in pages or page.url in ("", "about:blank"):
            return "needs_input", "Open an approved website first.", {"verified": False}
        validate_url(page.url, self.hosts, self.resolver)
        item.ctx.check()
        if item.operation == "inspect":
            return "succeeded", "Read the visible text from the current browser page.", {
                "verified": True, "url": page.url, "title": page.title()[:500], "text": _visible_text(page),
            }
        if item.operation == "search":
            query = str(item.args[0])
            locator = page.get_by_text(query, exact=False)
            count = min(locator.count(), MAX_SEARCH_RESULTS)
            matches = []
            for index in range(count):
                item.ctx.check()
                candidate = locator.nth(index)
                if candidate.is_visible():
                    matches.append(candidate.inner_text(timeout=1000)[:400])
            return "succeeded", f"Found {len(matches)} visible matches on the current page.", {
                "verified": True, "query": query, "matches": matches,
            }
        if item.operation == "click":
            link_text = str(item.args[0])
            links = page.get_by_role("link", name=link_text, exact=True)
            if links.count() != 1:
                return "needs_input", "NOVA could not find one visible link with that exact text.", {"verified": False}
            link = links.first
            if not link.is_visible():
                return "needs_input", "NOVA could not find one visible link with that exact text.", {"verified": False}
            target = link.get_attribute("href")
            if not target:
                return "needs_input", "That link has no destination NOVA can check.", {"verified": False}
            from urllib.parse import urljoin

            validate_url(urljoin(page.url, target), self.hosts, self.resolver)
            item.ctx.check()
            # Navigate to the checked destination directly. A page's click handler is untrusted code and can
            # submit forms or trigger an unrelated side effect.
            response = page.goto(validate_url(urljoin(page.url, target), self.hosts, self.resolver),
                                 wait_until="domcontentloaded")
            item.ctx.check()
            if response and not 200 <= response.status < 300:
                return "failed", "The link did not finish with a successful response; NOVA did not verify it opened.", {
                    "verified": False, "url": page.url, "http_status": response.status,
                }
            final = validate_url(page.url, self.hosts, self.resolver)
            return "succeeded", "Opened the approved link in the isolated browser.", {
                "verified": True, "url": final, "title": page.title(), "text": _visible_text(page),
            }
        return "denied", "Unknown browser operation.", {"verified": False}


def _visible_text(page: Any) -> str:
    return page.locator("body").inner_text(timeout=2000)[:MAX_PAGE_TEXT]
