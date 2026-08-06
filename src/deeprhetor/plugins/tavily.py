"""Tavily general-web discovery adapter (candidates only; archive via fetch)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from deeprhetor.config.settings import AppConfig, LimitsConfig
from deeprhetor.domain.discovery import SearchHit, SearchRequest, SearchResponse
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.rate_limit import ProviderGate

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
PROVIDER_NAME = "tavily"
PROVIDER_VERSION = "1.0.0"

TAVILY_DESCRIPTOR = ProviderDescriptor(
    name=PROVIDER_NAME,
    version=PROVIDER_VERSION,
    source_classes=["web", "general"],
    supports_freshness=True,
    supports_date_filter=False,
    languages=["en"],
    domains=["tavily.com"],
    requires_auth=True,
    rate_limit_per_minute=30,
    max_results=20,
    returns="references",
    licensing_notes=(
        "Tavily is used for discovery metadata only. Page bodies returned by Tavily "
        "must not be treated as the permanent corpus archive; use DeepRhetor's "
        "secure fetch + parser pipeline for archival."
    ),
)


class TavilyConfigError(RuntimeError):
    """Raised when the Tavily API key is missing."""


class TavilySearchProvider:
    """Paid discovery provider. Returns candidates/metadata, not archive bytes."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config: AppConfig | None = None,
        limits: LimitsConfig | None = None,
        search_url: str = TAVILY_SEARCH_URL,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        gate: ProviderGate | None = None,
    ) -> None:
        resolved_key = api_key
        if resolved_key is None and config is not None:
            resolved_key = config.providers.tavily.api_key.get_secret_value() or None
        self.api_key = (resolved_key or "").strip()
        self.search_url = search_url
        self.descriptor = TAVILY_DESCRIPTOR
        self._client = client
        self._transport = transport
        lim = limits or (config.limits if config is not None else LimitsConfig())
        self._gate = gate or ProviderGate(
            rate_per_minute=lim.tavily_rate_limit_per_minute,
            concurrency=lim.provider_concurrency,
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        if not self.api_key:
            raise TavilyConfigError(
                "Tavily API key required (providers.tavily.api_key or "
                "DEEPRHETOR_TAVILY_API_KEY)"
            )
        max_results = min(
            request.max_results,
            self.descriptor.max_results or 20,
        )
        payload = {
            "api_key": self.api_key,
            "query": request.query,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "search_depth": "basic",
        }
        if request.freshness_days is not None:
            # Tavily days filter is approximate; expose intent in metadata.
            payload["days"] = request.freshness_days

        data = await self._post_json(payload)
        hits: list[SearchHit] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title") or url or "Untitled"
            published = _parse_optional_datetime(item.get("published_date"))
            # Discovery only — retain snippet/score; never treat content as archive.
            hits.append(
                SearchHit(
                    hit_id=str(item.get("id") or uuid4()),
                    title=str(title),
                    url=str(url) if url else None,
                    snippet=item.get("content") or item.get("snippet"),
                    score=_as_float(item.get("score")),
                    published_at=published,
                    provider_metadata={
                        "discovery_only": True,
                        "archive_via": "deeprhetor.fetch",
                        "raw_content_ignored": True,
                        "tavily": {
                            k: item.get(k)
                            for k in ("score", "published_date", "favicon")
                            if k in item
                        },
                    },
                )
            )
        return SearchResponse(
            request=request,
            hits=hits,
            provider=PROVIDER_NAME,
            raw_ref="tavily:search",
        )

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        async with self._gate:
            if self._client is not None:
                response = await self._client.post(
                    self.search_url, json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
            else:
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=True
                ) as client:
                    response = await client.post(
                        self.search_url,
                        json=payload,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("unexpected Tavily response")
        return data


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None
