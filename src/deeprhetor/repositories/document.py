"""Minimal corpus repository stubs for document insert + FTS indexing."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import BaseRepository, loads_json, parse_dt, utcnow


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
    """Enough corpus write surface for Stage 2 FTS tests."""

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
        from deeprhetor.services.fts import FtsService

        did = document_id or str(uuid4())
        vid = version_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        joined = "\n\n".join(segments)
        content_sha = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        normalized_sha = content_sha

        segment_rows: list[DocumentSegment] = []
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO document (id, project_id, canonical_url, title, media_type, "
                    "source_class, created_at, metadata_json) "
                    "VALUES (:id, :project_id, :canonical_url, :title, :media_type, "
                    ":source_class, :created_at, '{}')"
                ),
                {
                    "id": did,
                    "project_id": project_id,
                    "canonical_url": canonical_url,
                    "title": title,
                    "media_type": media_type,
                    "source_class": source_class,
                    "created_at": iso,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO document_version (id, document_id, version, content_sha256, "
                    "normalized_sha256, parser, created_at, metadata_json) "
                    "VALUES (:id, :document_id, 1, :content_sha256, :normalized_sha256, "
                    "'plaintext', :created_at, '{}')"
                ),
                {
                    "id": vid,
                    "document_id": did,
                    "content_sha256": content_sha,
                    "normalized_sha256": normalized_sha,
                    "created_at": iso,
                },
            )
            offset = 0
            for idx, segment_text in enumerate(segments):
                sid = str(uuid4())
                char_start = offset
                char_end = offset + len(segment_text)
                offset = char_end + 2
                await conn.execute(
                    text(
                        "INSERT INTO document_segment "
                        "(id, document_version_id, segment_index, text, char_start, char_end, "
                        "status) "
                        "VALUES (:id, :document_version_id, :segment_index, :text, "
                        ":char_start, :char_end, 'pending')"
                    ),
                    {
                        "id": sid,
                        "document_version_id": vid,
                        "segment_index": idx,
                        "text": segment_text,
                        "char_start": char_start,
                        "char_end": char_end,
                    },
                )
                segment_rows.append(
                    DocumentSegment(
                        id=sid,
                        document_version_id=vid,
                        segment_index=idx,
                        text=segment_text,
                        char_start=char_start,
                        char_end=char_end,
                    )
                )

        if index_fts:
            fts = FtsService(self._engine)
            await fts.index_document_version(vid)

        document = Document(
            id=did,
            project_id=project_id,
            canonical_url=canonical_url,
            title=title,
            media_type=media_type,
            source_class=source_class,
            created_at=now,
        )
        version = DocumentVersion(
            id=vid,
            document_id=did,
            version=1,
            content_sha256=content_sha,
            normalized_sha256=normalized_sha,
            parser="plaintext",
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
