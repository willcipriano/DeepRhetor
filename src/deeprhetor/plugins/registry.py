"""Registry for pluggable search providers."""

from __future__ import annotations

from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.protocols import SearchProvider


class SearchProviderRegistry:
    """Name-keyed registry of capability-aware search providers."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> None:
        name = provider.descriptor.name
        if not name:
            raise ValueError("provider descriptor name is required")
        self._providers[name] = provider

    def get(self, name: str) -> SearchProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"unknown search provider: {name}") from exc

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._providers)

    def descriptors(self) -> list[ProviderDescriptor]:
        return [p.descriptor for p in self._providers.values()]

    def list_providers(self) -> list[SearchProvider]:
        return [self._providers[name] for name in self.names()]

    def __contains__(self, name: str) -> bool:
        return name in self._providers

    def __len__(self) -> int:
        return len(self._providers)


def create_default_registry(config: AppConfig | None = None) -> SearchProviderRegistry:
    """Build a registry with MVP source adapters enabled.

    Crossref is registered for enrichment/metadata search but uses
    ``source_classes=["bibliographic"]`` so the capability-aware dispatcher
    does not treat it as a default scholarly worker assignment.
    """
    from deeprhetor.plugins.arxiv import ArxivSearchProvider
    from deeprhetor.plugins.crossref import CrossrefEnricher
    from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider
    from deeprhetor.plugins.openalex import OpenAlexSearchProvider
    from deeprhetor.plugins.tavily import TavilySearchProvider

    registry = SearchProviderRegistry()
    registry.register(MediaWikiSearchProvider())
    registry.register(OpenAlexSearchProvider(config=config))
    registry.register(ArxivSearchProvider(config=config))
    registry.register(CrossrefEnricher(config=config))
    api_key = None
    if config is not None:
        api_key = config.providers.tavily.api_key.get_secret_value() or None
    registry.register(TavilySearchProvider(api_key=api_key, config=config))
    return registry
