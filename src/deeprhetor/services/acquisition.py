"""Hard acquisition path: secure fetch, parser, Playwright + OCR fallbacks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deeprhetor.domain.sources import FetchRequest, FetchResult, ParsedDocument, RawDocument
from deeprhetor.plugins.parsers import ParserRegistry, default_parser_registry
from deeprhetor.plugins.playwright_fetch import PlaywrightFetcher, PlaywrightUnavailableError
from deeprhetor.services.fetch import FetchError, SecureHttpFetcher


@dataclass
class AcquisitionResult:
    fetch: FetchResult
    parsed: ParsedDocument
    method: str
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _text_is_insufficient(parsed: ParsedDocument, *, min_chars: int = 80) -> bool:
    text = (parsed.text or "").strip()
    if len(text) < min_chars:
        return True
    # Heuristic: tiny text relative to many empty-ish segments
    if not parsed.segments:
        return True
    return False


class AcquisitionPipeline:
    """Archive candidates via secure HTTP, with Playwright fallback for JS pages."""

    def __init__(
        self,
        *,
        fetcher: SecureHttpFetcher | None = None,
        playwright: PlaywrightFetcher | None = None,
        parsers: ParserRegistry | None = None,
        min_chars: int = 80,
        enable_playwright: bool = True,
    ) -> None:
        self.fetcher = fetcher or SecureHttpFetcher()
        self.playwright = playwright or PlaywrightFetcher()
        self.parsers = parsers or default_parser_registry
        self.min_chars = min_chars
        self.enable_playwright = enable_playwright

    async def acquire(self, url: str, **fetch_kwargs: Any) -> AcquisitionResult:
        warnings: list[str] = []
        request = FetchRequest(url=url, **fetch_kwargs)
        fetch = await self.fetcher.fetch(request)
        raw = RawDocument(
            content=fetch.content,
            media_type=fetch.media_type,
            source_url=fetch.final_url,
            metadata={"sha256": fetch.sha256, "status_code": fetch.status_code},
        )
        parsed = await self.parsers.parse(raw)
        method = "http"

        needs_browser = (
            self.enable_playwright
            and fetch.media_type.split(";", 1)[0].strip().lower()
            in {"text/html", "application/xhtml+xml"}
            and _text_is_insufficient(parsed, min_chars=self.min_chars)
        )
        if needs_browser:
            try:
                rendered = await self.playwright.fetch(request)
                raw_js = RawDocument(
                    content=rendered.content,
                    media_type=rendered.media_type,
                    source_url=rendered.final_url,
                    metadata={"sha256": rendered.sha256, "fetcher": "playwright"},
                )
                parsed_js = await self.parsers.parse(raw_js)
                if not _text_is_insufficient(parsed_js, min_chars=self.min_chars) or len(
                    (parsed_js.text or "").strip()
                ) > len((parsed.text or "").strip()):
                    fetch = rendered
                    parsed = parsed_js
                    method = "playwright"
                else:
                    warnings.append("playwright_render_did_not_improve_text")
            except PlaywrightUnavailableError as exc:
                warnings.append(f"playwright_unavailable: {exc}")
            except FetchError as exc:
                warnings.append(f"playwright_failed: {exc}")

        return AcquisitionResult(
            fetch=fetch,
            parsed=parsed,
            method=method,
            warnings=warnings,
            metadata={
                "original_url": url,
                "final_url": fetch.final_url,
                "media_type": fetch.media_type,
            },
        )
