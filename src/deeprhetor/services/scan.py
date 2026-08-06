"""Resumable bounded-batch document segment scanning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.repositories.document import DocumentRepository, DocumentSegment
from deeprhetor.repositories.scan import DocumentScanRecord, ScanRepository


SegmentHandler = Callable[[DocumentSegment, int], Awaitable[dict[str, Any]]]


@dataclass
class BatchScanResult:
    document_version_id: str
    document_id: str
    batch_index: int
    scanned_segment_ids: list[str]
    remaining: int
    document_scan: DocumentScanRecord


class ScanService:
    """Process pending segments in bounded batches; resume-safe."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        batch_size: int = 10,
        documents: DocumentRepository | None = None,
        scans: ScanRepository | None = None,
    ) -> None:
        self._engine = engine
        self.batch_size = max(1, batch_size)
        self.documents = documents or DocumentRepository(engine)
        self.scans = scans or ScanRepository(engine)

    async def scan_batch(
        self,
        *,
        document_id: str,
        document_version_id: str,
        handler: SegmentHandler | None = None,
        task_id: str | None = None,
        batch_index: int = 0,
    ) -> BatchScanResult:
        segments = await self.documents.list_segments(document_version_id)
        await self.scans.ensure_document_scan(
            document_version_id=document_version_id,
            document_id=document_id,
            total_segments=len(segments),
        )
        pending_ids = await self.scans.list_unscanned_segment_ids(document_version_id)
        by_id = {s.id: s for s in segments}
        batch_ids = pending_ids[: self.batch_size]
        scanned: list[str] = []
        for sid in batch_ids:
            segment = by_id.get(sid)
            if segment is None:
                continue
            status = "completed"
            summary = None
            proposed: list[str] = []
            warnings: list[str] = []
            if handler is not None:
                outcome = await handler(segment, batch_index)
                status = str(outcome.get("status", "completed"))
                summary = outcome.get("summary")
                proposed = list(outcome.get("proposed_claim_ids") or [])
                warnings = list(outcome.get("warnings") or [])
            else:
                summary = f"scanned segment {segment.segment_index}"
            await self.scans.record_segment_scan(
                document_segment_id=sid,
                status=status,
                document_id=document_id,
                document_version_id=document_version_id,
                summary=summary,
                proposed_claim_ids=proposed,
                warnings=warnings,
                batch_index=batch_index,
                task_id=task_id,
            )
            scanned.append(sid)

        doc_scan = await self.scans.refresh_document_scan(
            document_version_id=document_version_id,
            document_id=document_id,
        )
        remaining = len(await self.scans.list_unscanned_segment_ids(document_version_id))
        return BatchScanResult(
            document_version_id=document_version_id,
            document_id=document_id,
            batch_index=batch_index,
            scanned_segment_ids=scanned,
            remaining=remaining,
            document_scan=doc_scan,
        )

    async def scan_until_complete(
        self,
        *,
        document_id: str,
        document_version_id: str,
        handler: SegmentHandler | None = None,
        task_id: str | None = None,
        max_batches: int | None = None,
    ) -> DocumentScanRecord:
        """Drain pending segments in batches until complete or max_batches hit."""
        batch_index = 0
        while True:
            result = await self.scan_batch(
                document_id=document_id,
                document_version_id=document_version_id,
                handler=handler,
                task_id=task_id,
                batch_index=batch_index,
            )
            if result.document_scan.is_complete or result.remaining == 0:
                return result.document_scan
            if not result.scanned_segment_ids:
                return result.document_scan
            batch_index += 1
            if max_batches is not None and batch_index >= max_batches:
                return result.document_scan
