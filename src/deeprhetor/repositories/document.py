"""Minimal corpus repository stubs for document insert + FTS indexing."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.sources import ParsedDocument, ParsedSegment

from .base import BaseRepository, dumps_json, loads_json, parse_dt, utcnow


class Document(BaseModel):
    id: str
    project_id: str
    canonical_url: str | None = None
    title: str | None = None
    media_type: str | None = None
    source_class: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentVersion(BaseModel):
    id: str
    document_id: str
    version: int = 1
    original_url: str | None = None
    content_sha256: str
    normalized_sha256: str | None = None
    parser: str | None = None
    parser_version: str | None = None
    created_at: datetime


class DocumentSegment(BaseModel):
    id: str
    document_version_id: str
    segment_index: int
    text: str
    page: int | None = None
    section_path: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    status: str = "pending"


class DocumentRepository(BaseRepository):
    """Corpus write surface for archive + FTS indexing."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create_with_version_and_segments(
        self,
        *,
        project_id: str,
        title: str,
        segments: Sequence[str],
        media_type: str = "text/plain",
        canonical_url: str | None = None,
        source_class: str | None = "local",
        document_id: str | None = None,
        version_id: str | None = None,
        index_fts: bool = True,
    ) -> tuple[Document, DocumentVersion, list[DocumentSegment]]:
        parsed_segments = []
        offset = 0
        for idx, segment_text in enumerate(segments):
            char_start = offset
            char_end = offset + len(segment_text)
            offset = char_end + 2
            parsed_segments.append(
                ParsedSegment(
                    text=segment_text,
                    char_start=char_start,
                    char_end=char_end,
                    status="pending",
                )
            )
        joined = "\n\n".join(segments)
        parsed = ParsedDocument(
            media_type=media_type,
            title=title,
            text=joined,
            segments=parsed_segments,
            parser="plaintext",
            parser_version="1.0.0",
        )
        return await self.archive_parsed(
            project_id=project_id,
            raw_content=joined.encode("utf-8"),
            parsed=parsed,
            media_type=media_type,
            canonical_url=canonical_url,
            source_class=source_class,
            title=title,
            document_id=document_id,
            version_id=version_id,
            index_fts=index_fts,
        )

    async def archive_parsed(
        self,
        *,
        project_id: str,
        raw_content: bytes,
        parsed: ParsedDocument,
        media_type: str,
        canonical_url: str | None = None,
        original_url: str | None = None,
        source_class: str | None = "local",
        title: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        index_fts: bool = True,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[Document, DocumentVersion, list[DocumentSegment]]:
        from deeprhetor.services.fts import FtsService

        did = document_id or str(uuid4())
        vid = version_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        content_sha = hashlib.sha256(raw_content).hexdigest()
        normalized_sha = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest()
        doc_title = title or parsed.title
        meta: dict[str, Any] = dict(extra_metadata or {})
        if parsed.source_metadata is not None:
            meta["source_metadata"] = parsed.source_metadata.model_dump(mode="json")
        meta_json = dumps_json(meta)

        segment_rows: list[DocumentSegment] = []
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO document (id, project_id, canonical_url, title, media_type, "
                    "source_class, created_at, metadata_json) "
                    "VALUES (:id, :project_id, :canonical_url, :title, :media_type, "
                    ":source_class, :created_at, :metadata_json)"
                ),
                {
                    "id": did,
                    "project_id": project_id,
                    "canonical_url": canonical_url,
                    "title": doc_title,
                    "media_type": media_type,
                    "source_class": source_class,
                    "created_at": iso,
                    "metadata_json": meta_json,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO document_version (id, document_id, version, original_url, "
                    "content_sha256, normalized_sha256, parser, parser_version, created_at, "
                    "metadata_json) "
                    "VALUES (:id, :document_id, 1, :original_url, :content_sha256, "
                    ":normalized_sha256, :parser, :parser_version, :created_at, '{}')"
                ),
                {
                    "id": vid,
                    "document_id": did,
                    "original_url": original_url or canonical_url,
                    "content_sha256": content_sha,
                    "normalized_sha256": normalized_sha,
                    "parser": parsed.parser,
                    "parser_version": parsed.parser_version,
                    "created_at": iso,
                },
            )
            blob_id = str(uuid4())
            await conn.execute(
                text(
                    "INSERT INTO document_blob (id, document_version_id, kind, media_type, "
                    "compression, byte_size, sha256, data, created_at) "
                    "VALUES (:id, :document_version_id, 'original', :media_type, NULL, "
                    ":byte_size, :sha256, :data, :created_at)"
                ),
                {
                    "id": blob_id,
                    "document_version_id": vid,
                    "media_type": media_type,
                    "byte_size": len(raw_content),
                    "sha256": content_sha,
                    "data": raw_content,
                    "created_at": iso,
                },
            )
            for idx, segment in enumerate(parsed.segments):
                sid = str(uuid4())
                await conn.execute(
                    text(
                        "INSERT INTO document_segment "
                        "(id, document_version_id, segment_index, text, page, section_path, "
                        "char_start, char_end, status) "
                        "VALUES (:id, :document_version_id, :segment_index, :text, :page, "
                        ":section_path, :char_start, :char_end, :status)"
                    ),
                    {
                        "id": sid,
                        "document_version_id": vid,
                        "segment_index": idx,
                        "text": segment.text,
                        "page": segment.page,
                        "section_path": segment.section_path,
                        "char_start": segment.char_start,
                        "char_end": segment.char_end,
                        "status": segment.status,
                    },
                )
                segment_rows.append(
                    DocumentSegment(
                        id=sid,
                        document_version_id=vid,
                        segment_index=idx,
                        text=segment.text,
                        page=segment.page,
                        section_path=segment.section_path,
                        char_start=segment.char_start,
                        char_end=segment.char_end,
                        status=segment.status,
                    )
                )

        if index_fts:
            fts = FtsService(self._engine)
            await fts.index_document_version(vid)

        document = Document(
            id=did,
            project_id=project_id,
            canonical_url=canonical_url,
            title=doc_title,
            media_type=media_type,
            source_class=source_class,
            created_at=now,
            metadata=meta,
        )
        version = DocumentVersion(
            id=vid,
            document_id=did,
            version=1,
            original_url=original_url or canonical_url,
            content_sha256=content_sha,
            normalized_sha256=normalized_sha,
            parser=parsed.parser,
            parser_version=parsed.parser_version,
            created_at=now,
        )
        return document, version, segment_rows

    async def get(self, document_id: str) -> Document | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, canonical_url, title, media_type, source_class, "
                    "created_at, metadata_json FROM document WHERE id = :id"
                ),
                {"id": document_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return Document(
            id=row["id"],
            project_id=row["project_id"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            media_type=row["media_type"],
            source_class=row["source_class"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            metadata=loads_json(row["metadata_json"]),
        )

    async def list_segments(self, document_version_id: str) -> list[DocumentSegment]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, document_version_id, segment_index, text, page, section_path, "
                    "char_start, char_end, status FROM document_segment "
                    "WHERE document_version_id = :vid ORDER BY segment_index"
                ),
                {"vid": document_version_id},
            )
            rows = result.mappings().all()
        return [
            DocumentSegment(
                id=row["id"],
                document_version_id=row["document_version_id"],
                segment_index=row["segment_index"],
                text=row["text"],
                page=row["page"],
                section_path=row["section_path"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                status=row["status"],
            )
            for row in rows
        ]
