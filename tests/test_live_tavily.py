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
    response = await provider.search(
        SearchRequest(
            query="Aristotle rhetoric inventio",
            provider="tavily",
            max_results=3,
        )
    )
    assert response.hits, "Tavily returned no hits"
    hit = next((h for h in response.hits if h.url), None)
    assert hit is not None and hit.url
    assert hit.provider_metadata.get("discovery_only") is True

    # Archive via DeepRhetor secure fetch — not Tavily page body.
    async with SecureHttpFetcher(
        timeout_seconds=float(config.limits.fetch_timeout_seconds),
        max_bytes=config.limits.max_document_bytes,
    ) as fetcher:
        archived = await fetcher.fetch(FetchRequest(url=hit.url, max_bytes=2_000_000))
    assert archived.byte_size > 0
    assert archived.sha256
    assert archived.status_code == 200
