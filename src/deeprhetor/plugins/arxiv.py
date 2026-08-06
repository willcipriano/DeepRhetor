"""arXiv preprint discovery with strict rate limiting."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from deeprhetor.config.settings import AppConfig, LimitsConfig
from deeprhetor.domain.discovery import SearchHit, SearchRequest, SearchResponse
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.rate_limit import ProviderGate

ARXIV_API_URL = "https://export.arxiv.org/api/query"
PROVIDER_NAME = "arxiv"
PROVIDER_VERSION = "1.0.0"
USER_AGENT = "DeepRhetor/0.1 (research; mailto:local@localhost)"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

ARXIV_DESCRIPTOR = ProviderDescriptor(
    name=PROVIDER_NAME,
    version=PROVIDER_VERSION,
    source_classes=["preprint", "scholarly"],
    supports_freshness=False,
    supports_date_filter=False,
    languages=["en"],
    domains=["arxiv.org"],
    requires_auth=False,
    rate_limit_per_minute=20,
    max_results=50,
    returns="references",
    licensing_notes=(
        "arXiv content is subject to each author's license. Respect the polite "
        "API pool (strict rate limiting). Prefer the abs/PDF URLs for archival."
    ),
)


class ArxivSearchProvider:
    """Specialized preprint discovery; rate-limited for the arXiv polite pool."""

    def __init__(
        self,
        *,
        api_url: str = ARXIV_API_URL,
        config: AppConfig | None = None,
        limits: LimitsConfig | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        gate: ProviderGate | None = None,
    ) -> None:
        self.api_url = api_url
        self.descriptor = ARXIV_DESCRIPTOR
        self._client = client
        self._transport = transport
        lim = limits or (config.limits if config is not None else LimitsConfig())
        # Cap harder than config if someone raises the limit above polite guidance.
        rpm = min(lim.arxiv_rate_limit_per_minute, 20)
        self._gate = gate or ProviderGate(
            rate_per_minute=rpm,
            concurrency=min(1, lim.provider_concurrency),
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        max_results = min(request.max_results, self.descriptor.max_results or 50)
        # Prefer all: query; callers can pass a raw arXiv syntax string.
        query = request.query.strip()
        if ":" not in query:
            query = f"all:{query}"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        xml_text = await self._get_text(params)
        hits = _parse_atom(xml_text)
        return SearchResponse(
            request=request,
            hits=hits,
            provider=PROVIDER_NAME,
            raw_ref="arxiv:atom",
        )

    async def _get_text(self, params: dict[str, Any]) -> str:
        headers = {"User-Agent": USER_AGENT}
        async with self._gate:
            if self._client is not None:
                response = await self._client.get(
                    self.api_url, params=params, headers=headers
                )
                response.raise_for_status()
                return response.text
            async with httpx.AsyncClient(
                transport=self._transport, follow_redirects=True
            ) as client:
                response = await client.get(
                    self.api_url, params=params, headers=headers, timeout=30.0
                )
                response.raise_for_status()
                return response.text


def _parse_atom(xml_text: str) -> list[SearchHit]:
    root = ET.fromstring(xml_text)
    hits: list[SearchHit] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        title = " ".join(
            (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split()
        )
        summary = " ".join(
            (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").split()
        )
        published_raw = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
        pdf_url = None
        abs_url = None
        for link in entry.findall("atom:link", ATOM_NS):
            href = link.attrib.get("href")
            rel = link.attrib.get("rel")
            title_attr = link.attrib.get("title")
            if not href:
                continue
            if title_attr == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = href
            if rel == "alternate":
                abs_url = href
        arxiv_id = entry_id.rsplit("/", 1)[-1] if entry_id else quote_plus(title)
        hits.append(
            SearchHit(
                hit_id=arxiv_id,
                title=title or arxiv_id,
                url=abs_url or entry_id or None,
                snippet=summary[:500] if summary else None,
                score=None,
                published_at=_parse_iso(published_raw),
                provider_metadata={
                    "arxiv_id": arxiv_id,
                    "entry_id": entry_id,
                    "pdf_url": pdf_url,
                    "authors": [
                        (a.findtext("atom:name", default="", namespaces=ATOM_NS) or "").strip()
                        for a in entry.findall("atom:author", ATOM_NS)
                    ],
                    "primary_category": _primary_category(entry),
                },
            )
        )
    return hits


def _primary_category(entry: ET.Element) -> str | None:
    primary = entry.find("arxiv:primary_category", ATOM_NS)
    if primary is not None and primary.attrib.get("term"):
        return primary.attrib["term"]
    cat = entry.find("atom:category", ATOM_NS)
    if cat is not None:
        return cat.attrib.get("term")
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
