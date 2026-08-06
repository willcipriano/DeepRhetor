"""Registry for pluggable search providers."""

from __future__ import annotations

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


def create_default_registry() -> SearchProviderRegistry:
    """Build a registry with MVP free-source adapters enabled."""
    from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider

    registry = SearchProviderRegistry()
    registry.register(MediaWikiSearchProvider())
    return registry
