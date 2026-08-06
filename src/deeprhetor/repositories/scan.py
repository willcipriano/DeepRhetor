"""Segment and document scan accounting repositories."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.assessment import DocumentScanSummary, SegmentScanResult

from .base import BaseRepository, dumps_json, loads_json, parse_dt, utcnow

# Segment scans are terminal once completed or failed (or explicitly skipped).
TERMINAL_SCAN_STATUSES = frozenset({"completed", "failed", "skipped"})
NONTERMINAL_SCAN_STATUSES = frozenset({"pending", "running"})


class SegmentScanRecord(BaseModel):
    id: str
    document_segment_id: str
    task_id: str | None = None
    status: str = "completed"
    summary: str | None = None
    result: SegmentScanResult | None = None
    batch_index: int = 0
    created_at: datetime


class DocumentScanRecord(BaseModel):
    id: str
    document_version_id: str
    total_segments: int = 0
    completed_segments: int = 0
    failed_segments: int = 0
    is_complete: bool = False
    summary: DocumentScanSummary | None = None
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScanRepository(BaseRepository):
    """Resumable segment-batch scan accounting."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def ensure_document_scan(
        self,
        *,
        document_version_id: str,
        document_id: str,
        total_segments: int,
        scan_id: str | None = None,
    ) -> DocumentScanRecord:
        existing = await self.get_document_scan(document_version_id)
        if existing is not None:
            return existing
        sid = scan_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        summary = DocumentScanSummary(
            id=sid,
            document_id=document_id,
            document_version_id=document_version_id,
            total_segments=total_segments,
            completed_segments=0,
            failed_segments=0,
            is_complete=False,
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO document_scan "
                    "(id, document_version_id, total_segments, completed_segments, "
                    "failed_segments, is_complete, summary_json, updated_at) "
                    "VALUES (:id, :document_version_id, :total_segments, 0, 0, 0, "
                    ":summary_json, :updated_at)"
                ),
                {
                    "id": sid,
                    "document_version_id": document_version_id,
                    "total_segments": total_segments,
                    "summary_json": dumps_json(summary.model_dump(mode="json")),
                    "updated_at": iso,
                },
            )
        return DocumentScanRecord(
            id=sid,
            document_version_id=document_version_id,
            total_segments=total_segments,
            updated_at=now,
            summary=summary,
        )

    async def get_document_scan(self, document_version_id: str) -> DocumentScanRecord | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, document_version_id, total_segments, completed_segments, "
                    "failed_segments, is_complete, summary_json, updated_at "
                    "FROM document_scan WHERE document_version_id = :vid"
                ),
                {"vid": document_version_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        payload = loads_json(row["summary_json"], default={})
        summary = (
            DocumentScanSummary.model_validate(payload)
            if payload
            else None
        )
        return DocumentScanRecord(
            id=row["id"],
            document_version_id=row["document_version_id"],
            total_segments=row["total_segments"],
            completed_segments=row["completed_segments"],
            failed_segments=row["failed_segments"],
            is_complete=bool(row["is_complete"]),
            summary=summary,
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )

    async def list_unscanned_segment_ids(self, document_version_id: str) -> list[str]:
        """Return segment IDs that lack a terminal scan row."""
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT ds.id FROM document_segment ds "
                    "WHERE ds.document_version_id = :vid "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM segment_scan ss "
                    "  WHERE ss.document_segment_id = ds.id "
                    "  AND ss.status IN ('completed', 'failed', 'skipped')"
                    ") "
                    "ORDER BY ds.segment_index"
                ),
                {"vid": document_version_id},
            )
            rows = result.fetchall()
        return [row[0] for row in rows]

    async def record_segment_scan(
        self,
        *,
        document_segment_id: str,
        status: str,
        document_id: str,
        document_version_id: str,
        summary: str | None = None,
        proposed_claim_ids: list[str] | None = None,
        warnings: list[str] | None = None,
        batch_index: int = 0,
        task_id: str | None = None,
        scan_id: str | None = None,
    ) -> SegmentScanRecord:
        if status not in TERMINAL_SCAN_STATUSES | NONTERMINAL_SCAN_STATUSES:
            raise ValueError(f"unknown scan status: {status}")
        sid = scan_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        result = SegmentScanResult(
            id=sid,
            document_id=document_id,
            document_version_id=document_version_id,
            segment_id=document_segment_id,
            status=status,
            summary=summary,
            proposed_claim_ids=proposed_claim_ids or [],
            warnings=warnings or [],
            batch_index=batch_index,
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO segment_scan "
                    "(id, document_segment_id, task_id, status, summary, result_json, "
                    "batch_index, created_at) "
                    "VALUES (:id, :document_segment_id, :task_id, :status, :summary, "
                    ":result_json, :batch_index, :created_at)"
                ),
                {
                    "id": sid,
                    "document_segment_id": document_segment_id,
                    "task_id": task_id,
                    "status": status,
                    "summary": summary,
                    "result_json": dumps_json(result.model_dump(mode="json")),
                    "batch_index": batch_index,
                    "created_at": iso,
                },
            )
        return SegmentScanRecord(
            id=sid,
            document_segment_id=document_segment_id,
            task_id=task_id,
            status=status,
            summary=summary,
            result=result,
            batch_index=batch_index,
            created_at=now,
        )

    async def refresh_document_scan(
        self,
        *,
        document_version_id: str,
        document_id: str,
    ) -> DocumentScanRecord:
        """Recompute accounting; document is complete only when ALL segments are terminal."""
        await self.ensure_document_scan(
            document_version_id=document_version_id,
            document_id=document_id,
            total_segments=0,
        )
        async with self.connection() as conn:
            total_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM document_segment "
                    "WHERE document_version_id = :vid"
                ),
                {"vid": document_version_id},
            )
            total = int(total_result.scalar_one() or 0)
            # Latest terminal status per segment (most recent created_at).
            completed_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT document_segment_id, status FROM segment_scan ss "
                    "  WHERE status IN ('completed', 'failed', 'skipped') "
                    "  AND document_segment_id IN ("
                    "    SELECT id FROM document_segment WHERE document_version_id = :vid"
                    "  )"
                    "  GROUP BY document_segment_id"
                    ")"
                ),
                {"vid": document_version_id},
            )
            terminal_count = int(completed_result.scalar_one() or 0)
            failed_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT document_segment_id FROM segment_scan "
                    "  WHERE status = 'failed' "
                    "  AND document_segment_id IN ("
                    "    SELECT id FROM document_segment WHERE document_version_id = :vid"
                    "  )"
                    "  GROUP BY document_segment_id"
                    ")"
                ),
                {"vid": document_version_id},
            )
            failed = int(failed_result.scalar_one() or 0)
            ok_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT document_segment_id FROM segment_scan "
                    "  WHERE status IN ('completed', 'skipped') "
                    "  AND document_segment_id IN ("
                    "    SELECT id FROM document_segment WHERE document_version_id = :vid"
                    "  )"
                    "  GROUP BY document_segment_id"
                    ")"
                ),
                {"vid": document_version_id},
            )
            completed = int(ok_result.scalar_one() or 0)

        is_complete = total > 0 and terminal_count >= total
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        existing = await self.get_document_scan(document_version_id)
        assert existing is not None
        summary = DocumentScanSummary(
            id=existing.id,
            document_id=document_id,
            document_version_id=document_version_id,
            total_segments=total,
            completed_segments=completed,
            failed_segments=failed,
            is_complete=is_complete,
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE document_scan SET total_segments = :total, "
                    "completed_segments = :completed, failed_segments = :failed, "
                    "is_complete = :is_complete, summary_json = :summary_json, "
                    "updated_at = :updated_at "
                    "WHERE document_version_id = :vid"
                ),
                {
                    "vid": document_version_id,
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "is_complete": 1 if is_complete else 0,
                    "summary_json": dumps_json(summary.model_dump(mode="json")),
                    "updated_at": iso,
                },
            )
        refreshed = await self.get_document_scan(document_version_id)
        assert refreshed is not None
        return refreshed

    async def list_incomplete_document_scans(self) -> list[DocumentScanRecord]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, document_version_id, total_segments, completed_segments, "
                    "failed_segments, is_complete, summary_json, updated_at "
                    "FROM document_scan WHERE is_complete = 0"
                )
            )
            rows = result.mappings().all()
        out: list[DocumentScanRecord] = []
        for row in rows:
            payload = loads_json(row["summary_json"], default={})
            out.append(
                DocumentScanRecord(
                    id=row["id"],
                    document_version_id=row["document_version_id"],
                    total_segments=row["total_segments"],
                    completed_segments=row["completed_segments"],
                    failed_segments=row["failed_segments"],
                    is_complete=bool(row["is_complete"]),
                    summary=DocumentScanSummary.model_validate(payload) if payload else None,
                    updated_at=parse_dt(row["updated_at"]) or utcnow(),
                )
            )
        return out
