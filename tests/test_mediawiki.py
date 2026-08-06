"""MediaWiki adapter tests using recorded fixtures (no live network)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from deeprhetor.domain.discovery import SearchRequest
from deeprhetor.domain.sources import FetchRequest
from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider
from deeprhetor.plugins.registry import create_default_registry
from deeprhetor.services.mediawiki_import import MediaWikiImporter
from deeprhetor.services.project_store import create_project_async


@pytest.fixture
def mediawiki_fixtures(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures" / "mediawiki"


def _mock_transport(fixtures: Path) -> httpx.MockTransport:
    search = json.loads((fixtures / "search_rhetoric.json").read_text(encoding="utf-8"))
    extract = json.loads((fixtures / "extract_rhetoric.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        if params.get("list") == ["search"]:
            return httpx.Response(200, json=search)
        if "extracts" in (params.get("prop") or [""])[0]:
            return httpx.Response(200, json=extract)
        return httpx.Response(404, json={"error": "unexpected"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_mediawiki_search_fixture(mediawiki_fixtures: Path) -> None:
    transport = _mock_transport(mediawiki_fixtures)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = MediaWikiSearchProvider(client=client)
        response = await provider.search(
            SearchRequest(query="rhetoric", provider="mediawiki", max_results=5)
        )
    assert response.provider == "mediawiki"
    assert len(response.hits) == 2
    assert response.hits[0].title == "Rhetoric"
    assert "persuasion" in (response.hits[0].snippet or "").lower()
    assert "<span" not in (response.hits[1].snippet or "")


@pytest.mark.asyncio
async def test_mediawiki_fetch_and_parse_fixture(mediawiki_fixtures: Path) -> None:
    transport = _mock_transport(mediawiki_fixtures)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = MediaWikiSearchProvider(client=client)
        parsed = await provider.fetch_and_parse("Rhetoric")
        fetched = await provider.fetch(
            FetchRequest(url="https://en.wikipedia.org/wiki/Rhetoric")
        )
    assert parsed.title == "Rhetoric"
    assert parsed.source_metadata is not None
    assert parsed.source_metadata.license == "CC BY-SA 4.0"
    assert parsed.segments[0].section_path == "lead"
    assert fetched.media_type == "text/plain"
    assert b"persuasion" in fetched.content


@pytest.mark.asyncio
async def test_mediawiki_import_into_archive(
    mediawiki_fixtures: Path, tmp_path: Path
) -> None:
    transport = _mock_transport(mediawiki_fixtures)
    path = tmp_path / "wiki.deeprhetor"
    opened = await create_project_async(path, title="Wiki", prompt="rhetoric")
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            provider = MediaWikiSearchProvider(client=client)
            importer = MediaWikiImporter(opened.engine, provider=provider)
            document, version, segments = await importer.import_title(
                "Rhetoric", project_id=opened.project.id
            )
        assert document.source_class == "encyclopedia"
        assert version.parser == "mediawiki-extract"
        assert segments
        assert all(s.status == "pending" for s in segments)
        assert document.metadata.get("license") == "CC BY-SA 4.0"
    finally:
        await opened.dispose()


def test_default_registry_includes_mediawiki() -> None:
    registry = create_default_registry()
    assert "mediawiki" in registry
    provider = registry.get("mediawiki")
    assert provider.descriptor.returns == "full_text"
    assert "encyclopedia" in provider.descriptor.source_classes
