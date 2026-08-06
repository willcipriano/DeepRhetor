"""Archive MediaWiki pages into the shared document pipeline."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider
from deeprhetor.repositories.document import Document, DocumentRepository, DocumentSegment, DocumentVersion


class MediaWikiImporter:
    """Search/fetch Wikipedia content and archive via DocumentRepository."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        provider: MediaWikiSearchProvider | None = None,
    ) -> None:
        self._provider = provider or MediaWikiSearchProvider()
        self._repo = DocumentRepository(engine)

    @property
    def provider(self) -> MediaWikiSearchProvider:
        return self._provider

    async def import_title(
        self,
        title: str,
        *,
        project_id: str,
        index_fts: bool = True,
    ) -> tuple[Document, DocumentVersion, list[DocumentSegment]]:
        parsed = await self._provider.fetch_and_parse(title)
        canonical = None
        if parsed.source_metadata and parsed.source_metadata.extra:
            canonical = parsed.source_metadata.extra.get("canonical_url")
        return await self._repo.archive_parsed(
            project_id=project_id,
            raw_content=parsed.text.encode("utf-8"),
            parsed=parsed,
            media_type="text/plain",
            canonical_url=canonical,
            original_url=canonical,
            source_class="encyclopedia",
            title=parsed.title or title,
            index_fts=index_fts,
            extra_metadata={
                "provider": "mediawiki",
                "license": "CC BY-SA 4.0",
                **dict(parsed.metadata),
            },
        )
