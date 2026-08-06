"""Search and source plugin adapters."""

from __future__ import annotations

from deeprhetor.plugins.mediawiki import MEDIAWIKI_DESCRIPTOR, MediaWikiSearchProvider
from deeprhetor.plugins.parsers import (
    DocxParser,
    HtmlParser,
    ParserRegistry,
    PdfParser,
    PlainTextParser,
    default_parser_registry,
    guess_media_type,
)
from deeprhetor.plugins.protocols import DocumentFetcher, DocumentParser, SearchProvider
from deeprhetor.plugins.registry import SearchProviderRegistry, create_default_registry

__all__ = [
    "MEDIAWIKI_DESCRIPTOR",
    "DocumentFetcher",
    "DocumentParser",
    "DocxParser",
    "HtmlParser",
    "MediaWikiSearchProvider",
    "ParserRegistry",
    "PdfParser",
    "PlainTextParser",
    "SearchProvider",
    "SearchProviderRegistry",
    "create_default_registry",
    "default_parser_registry",
    "guess_media_type",
]
