"""Search and source plugin adapters."""

from __future__ import annotations

from deeprhetor.plugins.arxiv import ARXIV_DESCRIPTOR, ArxivSearchProvider
from deeprhetor.plugins.crossref import CROSSREF_DESCRIPTOR, CrossrefEnricher, CrossrefSearchProvider
from deeprhetor.plugins.mediawiki import MEDIAWIKI_DESCRIPTOR, MediaWikiSearchProvider
from deeprhetor.plugins.openalex import OPENALEX_DESCRIPTOR, OpenAlexSearchProvider
from deeprhetor.plugins.parsers import (
    DocxParser,
    HtmlParser,
    ParserRegistry,
    PdfParser,
    PlainTextParser,
    default_parser_registry,
    guess_media_type,
)
from deeprhetor.plugins.playwright_fetch import PlaywrightFetcher, PlaywrightUnavailableError
from deeprhetor.plugins.protocols import DocumentFetcher, DocumentParser, SearchProvider
from deeprhetor.plugins.rate_limit import AsyncRateLimiter, ProviderGate
from deeprhetor.plugins.registry import SearchProviderRegistry, create_default_registry
from deeprhetor.plugins.tavily import TAVILY_DESCRIPTOR, TavilyConfigError, TavilySearchProvider

__all__ = [
    "ARXIV_DESCRIPTOR",
    "CROSSREF_DESCRIPTOR",
    "MEDIAWIKI_DESCRIPTOR",
    "OPENALEX_DESCRIPTOR",
    "TAVILY_DESCRIPTOR",
    "AsyncRateLimiter",
    "ArxivSearchProvider",
    "CrossrefEnricher",
    "CrossrefSearchProvider",
    "DocumentFetcher",
    "DocumentParser",
    "DocxParser",
    "HtmlParser",
    "MediaWikiSearchProvider",
    "OpenAlexSearchProvider",
    "ParserRegistry",
    "PdfParser",
    "PlainTextParser",
    "PlaywrightFetcher",
    "PlaywrightUnavailableError",
    "ProviderGate",
    "SearchProvider",
    "SearchProviderRegistry",
    "TavilyConfigError",
    "TavilySearchProvider",
    "create_default_registry",
    "default_parser_registry",
    "guess_media_type",
]
