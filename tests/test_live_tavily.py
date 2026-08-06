"""Optional live Tavily search + archive via DeepRhetor fetch.

Gated by environment / marker so default pytest stays offline:

    DEEPRHETOR_LIVE_TAVILY=1 py -3.12 -m pytest -m live

When enabled, config is loaded from the user config path
(%APPDATA%/deeprhetor/config.toml on Windows, or XDG elsewhere),
where the Tavily API key is expected to already be installed.
"""

from __future__ import annotations

import os

import pytest

from deeprhetor.config.loader import default_config_path, load_config
from deeprhetor.domain.discovery import SearchRequest
from deeprhetor.domain.sources import FetchRequest
from deeprhetor.plugins.tavily import TavilySearchProvider
from deeprhetor.services.fetch import SecureHttpFetcher

live = pytest.mark.live
live_tavily = pytest.mark.skipif(
    os.environ.get("DEEPRHETOR_LIVE_TAVILY") != "1",
    reason="Set DEEPRHETOR_LIVE_TAVILY=1 to run live Tavily integration",
)


@live
@live_tavily
@pytest.mark.asyncio
async def test_live_tavily_search_and_archive_fetch() -> None:
    config_path = default_config_path()
    assert config_path.is_file(), f"expected user config at {config_path}"
    config = load_config(config_path)
    key = config.providers.tavily.api_key.get_secret_value()
    assert key, f"providers.tavily.api_key missing in {config_path}"

    provider = TavilySearchProvider(config=config)
    # Prefer a query that returns absolute https URLs (some queries yield
    # Tavily /goto? redirect paths which the secure fetcher correctly rejects).
    response = await provider.search(
        SearchRequest(
            query="Aristotle Rhetoric Stanford Encyclopedia",
            provider="tavily",
            max_results=5,
        )
    )
    assert response.hits, "Tavily returned no hits"
    hit = next(
        (
            h
            for h in response.hits
            if h.url and h.url.startswith(("http://", "https://"))
        ),
        None,
    )
    if hit is None:
        # Retry with a simpler web query if the first batch was redirect-only.
        response = await provider.search(
            SearchRequest(
                query="Aristotle rhetoric",
                provider="tavily",
                max_results=5,
            )
        )
        hit = next(
            (
                h
                for h in response.hits
                if h.url and h.url.startswith(("http://", "https://"))
            ),
            None,
        )
    assert hit is not None and hit.url, "no hit with a public http(s) URL"
    assert hit.provider_metadata.get("discovery_only") is True

    # Archive via DeepRhetor secure fetch — not Tavily page body.
    # Try several candidates; sites may 403 bots.
    candidates = [
        h
        for h in response.hits
        if h.url and h.url.startswith(("http://", "https://"))
    ]
    assert candidates, "no archiveable candidates"
    last_error: Exception | None = None
    archived = None
    async with SecureHttpFetcher(
        timeout_seconds=float(config.limits.fetch_timeout_seconds),
        max_bytes=config.limits.max_document_bytes,
    ) as fetcher:
        for candidate in candidates:
            assert candidate.url
            try:
                archived = await fetcher.fetch(
                    FetchRequest(url=candidate.url, max_bytes=2_000_000)
                )
                if archived.status_code == 200 and archived.byte_size > 0:
                    break
            except Exception as exc:  # noqa: BLE001 — live flake tolerance
                last_error = exc
                archived = None
    assert archived is not None, f"all candidate fetches failed: {last_error!r}"
    assert archived.byte_size > 0
    assert archived.sha256
    assert archived.status_code == 200
