"""Headless Chromium fallback for JavaScript-rendered HTML."""

from __future__ import annotations

import hashlib
from typing import Any

from deeprhetor.domain.sources import FetchRequest, FetchResult
from deeprhetor.services.fetch import FetchError, validate_public_url

PROVIDER_NAME = "playwright"
DEFAULT_TIMEOUT_MS = 60_000


class PlaywrightUnavailableError(FetchError):
    """Raised when the playwright optional dependency or browsers are missing."""


class PlaywrightFetcher:
    """Fetch rendered HTML via headless Chromium when static fetch is insufficient."""

    def __init__(
        self,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        headless: bool = True,
        browser_factory: Any | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._browser_factory = browser_factory

    async def fetch(self, request: FetchRequest) -> FetchResult:
        validate_public_url(request.url)
        timeout_ms = int(
            (request.timeout_seconds or self.timeout_ms / 1000.0) * 1000
        )
        html, final_url = await self._render(request.url, timeout_ms)
        content = html.encode("utf-8")
        max_bytes = request.max_bytes
        if max_bytes is not None and len(content) > max_bytes:
            raise FetchError(f"rendered HTML exceeds max_bytes {max_bytes}")
        return FetchResult(
            original_url=request.url,
            final_url=final_url or request.url,
            media_type="text/html",
            content=content,
            headers={"content-type": "text/html; charset=utf-8", "x-fetcher": PROVIDER_NAME},
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status_code=200,
        )

    async def _render(self, url: str, timeout_ms: int) -> tuple[str, str]:
        if self._browser_factory is not None:
            return await self._browser_factory(url, timeout_ms)

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise PlaywrightUnavailableError(
                "playwright is not installed; install with: pip install 'deeprhetor[playwright]' "
                "and run playwright install chromium"
            ) from exc

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                try:
                    page = await browser.new_page()
                    response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    if response is not None and response.status >= 400:
                        raise FetchError(f"HTTP {response.status} for {url}")
                    html = await page.content()
                    return html, page.url
                finally:
                    await browser.close()
        except PlaywrightUnavailableError:
            raise
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as fetch failure
            message = str(exc)
            if "Executable doesn't exist" in message or "browsers" in message.lower():
                raise PlaywrightUnavailableError(
                    "Playwright Chromium is not installed; run: playwright install chromium"
                ) from exc
            raise FetchError(f"playwright fetch failed: {exc}") from exc
