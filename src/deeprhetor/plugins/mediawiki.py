"""MediaWiki / Wikipedia search and fetch adapter."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx

from deeprhetor.domain.assessment import ParsedSourceMetadata
from deeprhetor.domain.discovery import SearchHit, SearchRequest, SearchResponse
from deeprhetor.domain.sources import (
    FetchRequest,
    FetchResult,
    ParsedDocument,
    ParsedSegment,
    ProviderDescriptor,
    RawDocument,
)
from deeprhetor.plugins.parsers import PARSER_VERSION, PlainTextParser
from deeprhetor.services.fetch import SecureHttpFetcher, validate_public_url

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
PROVIDER_NAME = "mediawiki"
PROVIDER_VERSION = "1.0.0"

MEDIAWIKI_DESCRIPTOR = ProviderDescriptor(
    name=PROVIDER_NAME,
    version=PROVIDER_VERSION,
    source_classes=["encyclopedia", "web"],
    supports_freshness=False,
    supports_date_filter=False,
    languages=["en"],
    domains=["wikipedia.org", "mediawiki.org"],
    requires_auth=False,
    rate_limit_per_minute=100,
    max_results=50,
    returns="full_text",
    licensing_notes=(
        "Wikipedia text is available under CC BY-SA 4.0 (and GFDL historically). "
        "Archive attributions and license notices with retrieved content."
    ),
)


class MediaWikiSearchProvider:
    """Search and retrieve Wikipedia content through the MediaWiki Action API."""

    def __init__(
        self,
        *,
        api_url: str = WIKIPEDIA_API,
        fetcher: SecureHttpFetcher | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = api_url
        self.descriptor = MEDIAWIKI_DESCRIPTOR
        self._fetcher = fetcher
        self._client = client
        self._transport = transport

    async def search(self, request: SearchRequest) -> SearchResponse:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": request.query,
            "srlimit": min(request.max_results, self.descriptor.max_results or 50),
            "format": "json",
            "utf8": 1,
        }
        data = await self._api_get(params)
        hits: list[SearchHit] = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title") or ""
            pageid = item.get("pageid")
            hits.append(
                SearchHit(
                    hit_id=str(pageid or uuid4()),
                    title=title,
                    url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    snippet=_strip_search_html(item.get("snippet") or ""),
                    score=None,
                    provider_metadata={
                        "pageid": pageid,
                        "wordcount": item.get("wordcount"),
                        "size": item.get("size"),
                        "timestamp": item.get("timestamp"),
                    },
                )
            )
        return SearchResponse(
            request=request,
            hits=hits,
            provider=PROVIDER_NAME,
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        """DocumentFetcher: resolve a Wikipedia URL/title and return extract bytes."""
        validate_public_url(request.url)
        title = title_from_wikipedia_url(request.url)
        page = await self._load_page(title)
        text = page.get("extract") or ""
        content = text.encode("utf-8")
        canonical = page.get("fullurl") or request.url
        return FetchResult(
            original_url=request.url,
            final_url=canonical,
            media_type="text/plain",
            content=content,
            headers={"content-type": "text/plain; charset=utf-8"},
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            status_code=200,
        )

    async def fetch_and_parse(self, title: str) -> ParsedDocument:
        page = await self._load_page(title)
        text = page.get("extract") or ""
        page_title = page.get("title") or title
        canonical = page.get("fullurl") or (
            f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
        )
        raw = RawDocument(
            content=text.encode("utf-8"),
            media_type="text/plain",
            filename=f"{page_title}.txt",
            source_url=canonical,
            title=page_title,
            metadata={
                "pageid": page.get("pageid"),
                "license": "CC BY-SA 4.0",
                "provider": PROVIDER_NAME,
            },
        )
        parsed = await PlainTextParser().parse(raw)
        segments: list[ParsedSegment] = []
        for idx, seg in enumerate(parsed.segments):
            section = "lead" if idx == 0 else seg.section_path
            segments.append(seg.model_copy(update={"section_path": section, "status": "pending"}))
        return parsed.model_copy(
            update={
                "title": page_title,
                "segments": segments,
                "parser": "mediawiki-extract",
                "parser_version": PARSER_VERSION,
                "source_metadata": ParsedSourceMetadata(
                    title=page_title,
                    publisher_or_site="Wikipedia",
                    license="CC BY-SA 4.0",
                    language="en",
                    identifiers={"pageid": str(page.get("pageid") or "")},
                    extra={"canonical_url": canonical},
                ),
                "metadata": {
                    "pageid": page.get("pageid"),
                    "license": "CC BY-SA 4.0",
                    "canonical_url": canonical,
                },
            }
        )

    async def _load_page(self, title: str) -> dict[str, Any]:
        params = {
            "action": "query",
            "prop": "extracts|info",
            "exintro": 0,
            "explaintext": 1,
            "titles": title,
            "inprop": "url",
            "format": "json",
            "utf8": 1,
            "redirects": 1,
        }
        data = await self._api_get(params)
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            raise LookupError(f"no MediaWiki pages for title={title!r}")
        page = next(iter(pages.values()))
        if "missing" in page:
            raise LookupError(f"MediaWiki page missing: {title!r}")
        return page

    async def _api_get(self, params: dict[str, Any]) -> dict[str, Any]:
        validate_public_url(self.api_url)
        headers = {"User-Agent": "DeepRhetor/0.1 (research; contact: local)"}
        if self._client is not None:
            response = await self._client.get(self.api_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("unexpected MediaWiki response")
            return data

        async with httpx.AsyncClient(transport=self._transport, follow_redirects=True) as client:
            response = await client.get(self.api_url, params=params, headers=headers, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("unexpected MediaWiki response")
            return data


def _strip_search_html(snippet: str) -> str:
    return re.sub(r"<[^>]+>", "", snippet)


def title_from_wikipedia_url(url: str) -> str:
    path = urlparse(url).path
    if "/wiki/" in path:
        return unquote(path.split("/wiki/", 1)[1].replace("_", " "))
    return unquote(path.rsplit("/", 1)[-1].replace("_", " "))
