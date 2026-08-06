"""OpenAlex scholarly discovery adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from deeprhetor.config.settings import AppConfig, LimitsConfig
from deeprhetor.domain.discovery import SearchHit, SearchRequest, SearchResponse
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.rate_limit import ProviderGate

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
PROVIDER_NAME = "openalex"
PROVIDER_VERSION = "1.0.0"
USER_AGENT = "DeepRhetor/0.1 (research; mailto:local@localhost)"

OPENALEX_DESCRIPTOR = ProviderDescriptor(
    name=PROVIDER_NAME,
    version=PROVIDER_VERSION,
    source_classes=["scholarly", "academic"],
    supports_freshness=False,
    supports_date_filter=True,
    languages=["en"],
    domains=["openalex.org"],
    requires_auth=False,
    rate_limit_per_minute=100,
    max_results=50,
    returns="references",
    licensing_notes=(
        "OpenAlex metadata is CC0. Prefer open-access PDF/HTML locations for "
        "archival via DeepRhetor fetch; do not invent full text from metadata."
    ),
)


class OpenAlexSearchProvider:
    """Broad scholarly discovery with open-access landing URLs when available."""

    def __init__(
        self,
        *,
        api_url: str = OPENALEX_WORKS_URL,
        config: AppConfig | None = None,
        limits: LimitsConfig | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        gate: ProviderGate | None = None,
        mailto: str = "local@localhost",
    ) -> None:
        self.api_url = api_url
        self.descriptor = OPENALEX_DESCRIPTOR
        self._client = client
        self._transport = transport
        self._mailto = mailto
        lim = limits or (config.limits if config is not None else LimitsConfig())
        self._gate = gate or ProviderGate(
            rate_per_minute=lim.openalex_rate_limit_per_minute,
            concurrency=lim.provider_concurrency,
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        per_page = min(request.max_results, self.descriptor.max_results or 50)
        params: dict[str, Any] = {
            "search": request.query,
            "per_page": per_page,
            "mailto": self._mailto,
        }
        data = await self._get_json(params)
        hits: list[SearchHit] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            title = item.get("display_name") or item.get("title") or "Untitled"
            openalex_id = item.get("id") or str(uuid4())
            doi = _doi_from_work(item)
            url = _best_url(item)
            published = _parse_date(item.get("publication_date"))
            hits.append(
                SearchHit(
                    hit_id=str(openalex_id),
                    title=str(title),
                    url=url,
                    snippet=_snippet(item),
                    score=None,
                    published_at=published,
                    provider_metadata={
                        "openalex_id": openalex_id,
                        "doi": doi,
                        "publication_year": item.get("publication_year"),
                        "cited_by_count": item.get("cited_by_count"),
                        "open_access": item.get("open_access"),
                        "primary_location": item.get("primary_location"),
                        "type": item.get("type"),
                    },
                )
            )
        return SearchResponse(
            request=request,
            hits=hits,
            provider=PROVIDER_NAME,
            raw_ref="openalex:works",
        )

    async def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"User-Agent": USER_AGENT}
        async with self._gate:
            if self._client is not None:
                response = await self._client.get(
                    self.api_url, params=params, headers=headers
                )
                response.raise_for_status()
                data = response.json()
            else:
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=True
                ) as client:
                    response = await client.get(
                        self.api_url,
                        params=params,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("unexpected OpenAlex response")
        return data


def _doi_from_work(item: dict[str, Any]) -> str | None:
    doi = item.get("doi")
    if isinstance(doi, str) and doi:
        return doi.removeprefix("https://doi.org/")
    ids = item.get("ids") or {}
    if isinstance(ids, dict) and ids.get("doi"):
        return str(ids["doi"]).removeprefix("https://doi.org/")
    return None


def _best_url(item: dict[str, Any]) -> str | None:
    oa = item.get("open_access") or {}
    if isinstance(oa, dict):
        oa_url = oa.get("oa_url")
        if oa_url:
            return str(oa_url)
    primary = item.get("primary_location") or {}
    if isinstance(primary, dict):
        for key in ("pdf_url", "landing_page_url"):
            if primary.get(key):
                return str(primary[key])
    if item.get("id"):
        return str(item["id"])
    doi = _doi_from_work(item)
    if doi:
        return f"https://doi.org/{doi}"
    return None


def _snippet(item: dict[str, Any]) -> str | None:
    abstract = item.get("abstract_inverted_index")
    if isinstance(abstract, dict) and abstract:
        return _reconstruct_abstract(abstract)[:500]
    return None


def _reconstruct_abstract(inverted: dict[str, Any]) -> str:
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        if not isinstance(idxs, list):
            continue
        for idx in idxs:
            try:
                positions.append((int(idx), str(word)))
            except (TypeError, ValueError):
                continue
    positions.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positions)


def _parse_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
