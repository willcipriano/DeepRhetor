"""Plugin protocol contracts for search, fetch, and parse."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from deeprhetor.domain.discovery import SearchRequest, SearchResponse
from deeprhetor.domain.sources import (
    FetchRequest,
    FetchResult,
    ParsedDocument,
    ProviderDescriptor,
    RawDocument,
)


@runtime_checkable
class SearchProvider(Protocol):
    descriptor: ProviderDescriptor

    async def search(self, request: SearchRequest) -> SearchResponse: ...


@runtime_checkable
class DocumentFetcher(Protocol):
    async def fetch(self, request: FetchRequest) -> FetchResult: ...


@runtime_checkable
class DocumentParser(Protocol):
    def supports(self, media_type: str) -> bool: ...

    async def parse(self, document: RawDocument) -> ParsedDocument: ...
