"""Crossref DOI / bibliographic metadata enrichment."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from deeprhetor.config.settings import AppConfig, LimitsConfig
from deeprhetor.domain.discovery import SearchHit, SearchRequest, SearchResponse
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.rate_limit import ProviderGate

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
PROVIDER_NAME = "crossref"
PROVIDER_VERSION = "1.0.0"
USER_AGENT = "DeepRhetor/0.1 (research; mailto:local@localhost)"

CROSSREF_DESCRIPTOR = ProviderDescriptor(
    name=PROVIDER_NAME,
    version=PROVIDER_VERSION,
    source_classes=["bibliographic"],
    supports_freshness=False,
    supports_date_filter=False,
    languages=["en"],
    domains=["crossref.org", "doi.org"],
    requires_auth=False,
    rate_limit_per_minute=50,
    max_results=50,
    returns="metadata",
    licensing_notes=(
        "Crossref provides bibliographic metadata. Prefer enrichment of discovered "
        "records rather than standalone worker search assignments."
    ),
    extra={"role": "enrichment"},
)


class CrossrefEnricher:
    """Enrich or look up bibliographic records by query or DOI.

    Crossref usually enriches discovered records rather than receiving a
    separate worker assignment; ``source_classes`` is ``bibliographic`` so the
    default capability-aware dispatcher will not assign ordinary scholarly
    topics to Crossref unless explicitly requested.
    """

    def __init__(
        self,
        *,
        api_url: str = CROSSREF_WORKS_URL,
        config: AppConfig | None = None,
        limits: LimitsConfig | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        gate: ProviderGate | None = None,
        mailto: str = "local@localhost",
    ) -> None:
        self.api_url = api_url
        self.descriptor = CROSSREF_DESCRIPTOR
        self._client = client
        self._transport = transport
        self._mailto = mailto
        lim = limits or (config.limits if config is not None else LimitsConfig())
        self._gate = gate or ProviderGate(
            rate_per_minute=lim.crossref_rate_limit_per_minute,
            concurrency=lim.provider_concurrency,
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Optional metadata search — not the primary Crossref workflow."""
        rows = min(request.max_results, self.descriptor.max_results or 50)
        params = {
            "query": request.query,
            "rows": rows,
            "mailto": self._mailto,
        }
        data = await self._get_json(self.api_url, params)
        message = data.get("message") or {}
        hits = [_hit_from_item(item) for item in message.get("items") or [] if isinstance(item, dict)]
        return SearchResponse(
            request=request,
            hits=hits,
            provider=PROVIDER_NAME,
            raw_ref="crossref:works",
        )

    async def enrich_doi(self, doi: str) -> dict[str, Any]:
        """Fetch bibliographic metadata for a DOI."""
        cleaned = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        url = f"{self.api_url.rstrip('/')}/{cleaned}"
        data = await self._get_json(url, {"mailto": self._mailto})
        message = data.get("message")
        if not isinstance(message, dict):
            raise LookupError(f"Crossref DOI not found: {cleaned}")
        return {
            "doi": message.get("DOI") or cleaned,
            "title": _first_title(message),
            "authors": _authors(message),
            "type": message.get("type"),
            "container_title": _first(message.get("container-title")),
            "published": _published_date(message),
            "url": message.get("URL") or f"https://doi.org/{cleaned}",
            "publisher": message.get("publisher"),
            "raw": message,
        }

    async def _get_json(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"User-Agent": USER_AGENT}
        async with self._gate:
            if self._client is not None:
                response = await self._client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            else:
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=True
                ) as client:
                    response = await client.get(
                        url, params=params, headers=headers, timeout=30.0
                    )
                    response.raise_for_status()
                    data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("unexpected Crossref response")
        return data


# Alias for registry / protocol use as a SearchProvider when needed.
CrossrefSearchProvider = CrossrefEnricher


def _hit_from_item(item: dict[str, Any]) -> SearchHit:
    doi = item.get("DOI")
    title = _first_title(item) or doi or "Untitled"
    return SearchHit(
        hit_id=str(doi or uuid4()),
        title=str(title),
        url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        snippet=_first(item.get("abstract")),
        score=None,
        published_at=_published_date(item),
        provider_metadata={
            "doi": doi,
            "type": item.get("type"),
            "publisher": item.get("publisher"),
            "container_title": _first(item.get("container-title")),
            "authors": _authors(item),
            "role": "enrichment",
        },
    )


def _first_title(item: dict[str, Any]) -> str | None:
    return _first(item.get("title"))


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return None


def _authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = author.get("given") or ""
        family = author.get("family") or ""
        name = f"{given} {family}".strip() or author.get("name")
        if name:
            names.append(str(name))
    return names


def _published_date(item: dict[str, Any]) -> datetime | None:
    for key in ("published-print", "published-online", "created"):
        block = item.get(key)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        nums = parts[0]
        try:
            year = int(nums[0])
            month = int(nums[1]) if len(nums) > 1 else 1
            day = int(nums[2]) if len(nums) > 2 else 1
            return datetime(year, month, day)
        except (TypeError, ValueError, IndexError):
            continue
    return None
