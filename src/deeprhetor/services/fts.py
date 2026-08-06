"""FTS5 index helpers for document segments and claims."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class DocumentFtsHit:
    segment_id: str
    document_version_id: str
    text: str
    rank: float | None = None


@dataclass(frozen=True)
class ClaimFtsHit:
    claim_id: str
    statement: str
    rank: float | None = None


class FtsService:
    """Sync/index helpers for ``document_fts`` and ``claim_fts`` virtual tables."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def clear_document_version(self, document_version_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM document_fts WHERE document_version_id = :vid"),
                {"vid": document_version_id},
            )

    async def index_document_version(self, document_version_id: str) -> int:
        """Replace FTS rows for a document version from ``document_segment`` text."""
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM document_fts WHERE document_version_id = :vid"),
                {"vid": document_version_id},
            )
            result = await conn.execute(
                text(
                    "INSERT INTO document_fts (segment_id, document_version_id, text) "
                    "SELECT id, document_version_id, text FROM document_segment "
                    "WHERE document_version_id = :vid"
                ),
                {"vid": document_version_id},
            )
            return result.rowcount or 0

    async def sync_document_segment(
        self, *, segment_id: str, document_version_id: str, text_value: str
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM document_fts WHERE segment_id = :sid"),
                {"sid": segment_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_fts (segment_id, document_version_id, text) "
                    "VALUES (:sid, :vid, :text)"
                ),
                {"sid": segment_id, "vid": document_version_id, "text": text_value},
            )

    async def search_documents(self, query: str, *, limit: int = 20) -> list[DocumentFtsHit]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT segment_id, document_version_id, text, rank "
                    "FROM document_fts WHERE document_fts MATCH :q "
                    "ORDER BY rank LIMIT :limit"
                ),
                {"q": query, "limit": limit},
            )
            rows = result.mappings().all()
        return [
            DocumentFtsHit(
                segment_id=row["segment_id"],
                document_version_id=row["document_version_id"],
                text=row["text"],
                rank=row["rank"],
            )
            for row in rows
        ]

    async def clear_claim(self, claim_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM claim_fts WHERE claim_id = :cid"),
                {"cid": claim_id},
            )

    async def index_claim(self, claim_id: str, statement: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM claim_fts WHERE claim_id = :cid"),
                {"cid": claim_id},
            )
            await conn.execute(
                text("INSERT INTO claim_fts (claim_id, statement) VALUES (:cid, :statement)"),
                {"cid": claim_id, "statement": statement},
            )

    async def sync_claims_for_project(self, project_id: str) -> int:
        """Rebuild claim FTS rows for every claim in a project."""
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM claim_fts WHERE claim_id IN "
                    "(SELECT id FROM claim WHERE project_id = :project_id)"
                ),
                {"project_id": project_id},
            )
            result = await conn.execute(
                text(
                    "INSERT INTO claim_fts (claim_id, statement) "
                    "SELECT id, statement FROM claim WHERE project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            return result.rowcount or 0

    async def search_claims(self, query: str, *, limit: int = 20) -> list[ClaimFtsHit]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT claim_id, statement, rank FROM claim_fts "
                    "WHERE claim_fts MATCH :q ORDER BY rank LIMIT :limit"
                ),
                {"q": query, "limit": limit},
            )
            rows = result.mappings().all()
        return [
            ClaimFtsHit(claim_id=row["claim_id"], statement=row["statement"], rank=row["rank"])
            for row in rows
        ]
