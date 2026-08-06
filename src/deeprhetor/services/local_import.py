"""Import local files into the project document archive."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.sources import RawDocument
from deeprhetor.plugins.parsers import ParserRegistry, default_parser_registry, guess_media_type
from deeprhetor.repositories.document import (
    Document,
    DocumentRepository,
    DocumentSegment,
    DocumentVersion,
)


class LocalFileImporter:
    """Parse a local PDF/HTML/MD/TXT/DOCX file and archive it via DocumentRepository."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        parsers: ParserRegistry | None = None,
    ) -> None:
        self._repo = DocumentRepository(engine)
        self._parsers = parsers or default_parser_registry

    async def import_path(
        self,
        path: Path | str,
        *,
        project_id: str,
        title: str | None = None,
        source_class: str = "local",
        index_fts: bool = True,
        max_bytes: int | None = None,
    ) -> tuple[Document, DocumentVersion, list[DocumentSegment]]:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(str(file_path))
        content = file_path.read_bytes()
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError(f"file exceeds max_bytes {max_bytes}")
        media_type = guess_media_type(file_path.name)
        raw = RawDocument(
            content=content,
            media_type=media_type,
            filename=file_path.name,
            title=title or file_path.stem,
            metadata={"path": str(file_path.resolve())},
        )
        return await self.import_raw(
            raw,
            project_id=project_id,
            source_class=source_class,
            index_fts=index_fts,
            max_bytes=max_bytes,
        )

    async def import_raw(
        self,
        raw: RawDocument,
        *,
        project_id: str,
        source_class: str = "local",
        index_fts: bool = True,
        canonical_url: str | None = None,
        max_bytes: int | None = None,
    ) -> tuple[Document, DocumentVersion, list[DocumentSegment]]:
        if max_bytes is not None and len(raw.content) > max_bytes:
            raise ValueError(f"file exceeds max_bytes {max_bytes}")
        parsed = await self._parsers.parse(raw)
        return await self._repo.archive_parsed(
            project_id=project_id,
            raw_content=raw.content,
            parsed=parsed,
            media_type=raw.media_type,
            canonical_url=canonical_url or raw.source_url,
            original_url=raw.source_url,
            source_class=source_class,
            title=parsed.title or raw.title,
            index_fts=index_fts,
            extra_metadata=dict(raw.metadata),
        )
