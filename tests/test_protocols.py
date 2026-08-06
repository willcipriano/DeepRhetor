"""Protocol and registry contract tests."""

from __future__ import annotations

import pytest

from deeprhetor.domain.discovery import SearchRequest, SearchResponse
from deeprhetor.domain.sources import (
    ParsedDocument,
    ProviderDescriptor,
    RawDocument,
)
from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider
from deeprhetor.plugins.parsers import PlainTextParser
from deeprhetor.plugins.protocols import DocumentFetcher, DocumentParser, SearchProvider
from deeprhetor.plugins.registry import SearchProviderRegistry
from deeprhetor.services.fetch import SecureHttpFetcher


class _StubSearch:
    descriptor = ProviderDescriptor(
        name="stub",
        version="0.0.1",
        source_classes=["web"],
        returns="references",
        licensing_notes="test only",
    )

    async def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(request=request, hits=[], provider=self.descriptor.name)


def test_search_provider_protocol_runtime_check() -> None:
    stub = _StubSearch()
    assert isinstance(stub, SearchProvider)
    wiki = MediaWikiSearchProvider()
    assert isinstance(wiki, SearchProvider)
    assert isinstance(wiki, DocumentFetcher)


def test_document_parser_protocol_runtime_check() -> None:
    parser = PlainTextParser()
    assert isinstance(parser, DocumentParser)


def test_document_fetcher_protocol_runtime_check() -> None:
    fetcher = SecureHttpFetcher()
    assert isinstance(fetcher, DocumentFetcher)


def test_provider_descriptor_fields() -> None:
    desc = ProviderDescriptor(
        name="example",
        version="1.2.3",
        source_classes=["scholarly", "web"],
        supports_freshness=True,
        supports_date_filter=True,
        languages=["en"],
        domains=["example.org"],
        requires_auth=True,
        rate_limit_per_minute=60,
        max_results=25,
        returns="metadata",
        licensing_notes="example license",
    )
    assert desc.name == "example"
    assert desc.returns == "metadata"
    assert desc.licensing_notes


def test_registry_register_get_list() -> None:
    registry = SearchProviderRegistry()
    registry.register(_StubSearch())
    assert len(registry) == 1
    assert registry.names() == ["stub"]
    assert registry.get("stub").descriptor.version == "0.0.1"
    assert registry.descriptors()[0].name == "stub"


@pytest.mark.asyncio
async def test_plaintext_parser_contract_async() -> None:
    parser = PlainTextParser()
    assert parser.supports("text/plain")
    assert not parser.supports("application/pdf")
    parsed = await parser.parse(
        RawDocument(content=b"Hello\n\nWorld", media_type="text/plain", filename="x.txt")
    )
    assert isinstance(parsed, ParsedDocument)
    assert len(parsed.segments) == 2
