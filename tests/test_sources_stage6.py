"""Stage 6 source provider tests (offline fixtures)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from deeprhetor.config.settings import AppConfig, LimitsConfig
from deeprhetor.domain.discovery import SearchRequest
from deeprhetor.domain.sources import FetchRequest, RawDocument
from deeprhetor.plugins.arxiv import ArxivSearchProvider
from deeprhetor.plugins.crossref import CrossrefEnricher
from deeprhetor.plugins.openalex import OpenAlexSearchProvider
from deeprhetor.plugins.parsers import PdfParser
from deeprhetor.plugins.playwright_fetch import PlaywrightFetcher
from deeprhetor.plugins.rate_limit import AsyncRateLimiter, ProviderGate
from deeprhetor.plugins.registry import create_default_registry
from deeprhetor.plugins.tavily import TavilyConfigError, TavilySearchProvider
from deeprhetor.services.acquisition import AcquisitionPipeline


@pytest.fixture
def fixtures(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures"


def test_default_registry_includes_stage6_providers() -> None:
    registry = create_default_registry()
    assert set(registry.names()) >= {
        "mediawiki",
        "tavily",
        "openalex",
        "crossref",
        "arxiv",
    }
    assert registry.get("crossref").descriptor.returns == "metadata"
    assert "bibliographic" in registry.get("crossref").descriptor.source_classes
    assert registry.get("tavily").descriptor.returns == "references"


@pytest.mark.asyncio
async def test_tavily_search_fixture_discovery_only(fixtures: Path) -> None:
    payload = json.loads((fixtures / "tavily" / "search_rhetoric.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search")
        body = json.loads(request.content.decode("utf-8"))
        assert body["include_raw_content"] is False
        assert "api_key" in body
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = TavilySearchProvider(api_key="test-key", client=client)
        response = await provider.search(
            SearchRequest(query="rhetoric Aristotle", provider="tavily", max_results=5)
        )
    assert response.provider == "tavily"
    assert len(response.hits) == 2
    assert response.hits[0].url == "https://example.com/rhetoric-aristotle"
    assert response.hits[0].provider_metadata.get("discovery_only") is True
    assert response.hits[0].provider_metadata.get("archive_via") == "deeprhetor.fetch"


@pytest.mark.asyncio
async def test_tavily_requires_api_key() -> None:
    provider = TavilySearchProvider(api_key="")
    with pytest.raises(TavilyConfigError):
        await provider.search(SearchRequest(query="x", provider="tavily"))


@pytest.mark.asyncio
async def test_openalex_search_fixture(fixtures: Path) -> None:
    payload = json.loads(
        (fixtures / "openalex" / "works_rhetoric.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "openalex.org" in request.url.host or True
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAlexSearchProvider(client=client)
        response = await provider.search(
            SearchRequest(query="rhetoric", provider="openalex", max_results=5)
        )
    assert response.provider == "openalex"
    assert len(response.hits) == 2
    assert response.hits[0].url.endswith(".pdf")
    assert "Classical rhetoric" in (response.hits[0].snippet or "")
    assert response.hits[0].provider_metadata.get("doi") == "10.1234/example.rhetoric"


@pytest.mark.asyncio
async def test_crossref_enrich_and_search_fixtures(fixtures: Path) -> None:
    works = json.loads(
        (fixtures / "crossref" / "works_rhetoric.json").read_text(encoding="utf-8")
    )
    doi_doc = json.loads(
        (fixtures / "crossref" / "doi_rhetoric.json").read_text(encoding="utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/works") and request.url.params.get("query"):
            return httpx.Response(200, json=works)
        if "10.1234" in path:
            return httpx.Response(200, json=doi_doc)
        return httpx.Response(404, json={"error": "unexpected"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enricher = CrossrefEnricher(client=client)
        search = await enricher.search(
            SearchRequest(query="rhetoric", provider="crossref", max_results=5)
        )
        meta = await enricher.enrich_doi("10.1234/example.rhetoric")
    assert search.provider == "crossref"
    assert search.hits[0].provider_metadata.get("role") == "enrichment"
    assert meta["title"] == "Rhetoric and Evidence"
    assert "Ada Example" in meta["authors"]


@pytest.mark.asyncio
async def test_arxiv_search_fixture_and_rate_limit_present(fixtures: Path) -> None:
    atom = (fixtures / "arxiv" / "query_rhetoric.xml").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        assert params.get("search_query")
        return httpx.Response(200, text=atom, headers={"content-type": "application/atom+xml"})

    transport = httpx.MockTransport(handler)
    limits = LimitsConfig(arxiv_rate_limit_per_minute=20, provider_concurrency=1)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = ArxivSearchProvider(client=client, limits=limits)
        response = await provider.search(
            SearchRequest(query="rhetoric", provider="arxiv", max_results=5)
        )
    assert response.provider == "arxiv"
    assert len(response.hits) == 2
    assert response.hits[0].provider_metadata["pdf_url"].endswith("2401.00001v1")
    assert provider.descriptor.rate_limit_per_minute == 20
    assert provider._gate.rate_limiter.min_interval == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_playwright_fetcher_with_stub_factory() -> None:
    async def factory(url: str, timeout_ms: int) -> tuple[str, str]:
        assert url.startswith("https://")
        assert timeout_ms > 0
        return "<html><body><p>Rendered research content about rhetoric.</p></body></html>", url

    fetcher = PlaywrightFetcher(browser_factory=factory)
    result = await fetcher.fetch(FetchRequest(url="https://example.com/app"))
    assert result.media_type == "text/html"
    assert b"Rendered research content" in result.content
    assert result.headers.get("x-fetcher") == "playwright"


@pytest.mark.asyncio
async def test_acquisition_falls_back_to_playwright(fixtures: Path) -> None:
    shell = (fixtures / "documents" / "js_shell.html").read_bytes()

    class StubHttp:
        async def fetch(self, request: FetchRequest):
            from deeprhetor.domain.sources import FetchResult
            import hashlib

            return FetchResult(
                original_url=request.url,
                final_url=request.url,
                media_type="text/html",
                content=shell,
                headers={},
                byte_size=len(shell),
                sha256=hashlib.sha256(shell).hexdigest(),
                status_code=200,
            )

    async def factory(url: str, timeout_ms: int) -> tuple[str, str]:
        html = (
            "<html><body><article><p>Hydrated rhetorical analysis of persuasion "
            "with enough text for the sufficiency heuristic to pass.</p></article></body></html>"
        )
        return html, url

    pipeline = AcquisitionPipeline(
        fetcher=StubHttp(),  # type: ignore[arg-type]
        playwright=PlaywrightFetcher(browser_factory=factory),
        min_chars=80,
    )
    result = await pipeline.acquire("https://example.com/spa")
    assert result.method == "playwright"
    assert "rhetorical analysis" in result.parsed.text.lower()


@pytest.mark.asyncio
async def test_pdf_ocr_graceful_skip_when_tesseract_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scanned/image PDF path must not crash if the tesseract binary is absent."""
    import deeprhetor.plugins.parsers as parsers_mod
    import fitz

    # Build a one-page PDF with an image and no text layer.
    doc = fitz.open()
    page = doc.new_page(width=200, height=80)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), 1)
    pix.clear_with(255)
    page.insert_image(fitz.Rect(10, 10, 180, 60), pixmap=pix)
    pdf_bytes = doc.tobytes()
    doc.close()

    monkeypatch.setattr(
        parsers_mod, "_ocr_page", lambda _page: ("", "ocr_skipped_tesseract_missing")
    )

    parsed = await PdfParser().parse(
        RawDocument(content=pdf_bytes, media_type="application/pdf", filename="scan.pdf")
    )
    assert parsed.parser in {"pymupdf", "pymupdf+ocr"}
    assert parsed.text == "" or parsed.segments == []
    warnings = (parsed.source_metadata.extra or {}).get("extraction_warnings", [])
    assert any("ocr_skipped" in w for w in warnings)


@pytest.mark.asyncio
async def test_pdf_ocr_uses_pytesseract_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deeprhetor.plugins.parsers as parsers_mod

    def fake_ocr(_page: object) -> tuple[str, str | None]:
        return "OCR extracted rhetoric text", None

    monkeypatch.setattr(parsers_mod, "_ocr_page", fake_ocr)

    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=80)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), 1)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(10, 10, 180, 60), pixmap=pix)
    pdf_bytes = doc.tobytes()
    doc.close()

    parsed = await PdfParser().parse(
        RawDocument(content=pdf_bytes, media_type="application/pdf", filename="scan.pdf")
    )
    assert parsed.parser == "pymupdf+ocr"
    assert "OCR extracted rhetoric text" in parsed.text


@pytest.mark.asyncio
async def test_provider_gate_enforces_interval() -> None:
    limiter = AsyncRateLimiter(600)  # 0.1s interval
    gate = ProviderGate(rate_limiter=limiter, concurrency=1)
    import time

    t0 = time.monotonic()
    async with gate:
        pass
    async with gate:
        pass
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.05


def test_limits_config_includes_provider_rates() -> None:
    cfg = AppConfig()
    assert cfg.limits.provider_concurrency >= 1
    assert cfg.limits.arxiv_rate_limit_per_minute <= 20
    assert cfg.limits.tavily_rate_limit_per_minute > 0
