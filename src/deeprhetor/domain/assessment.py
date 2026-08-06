"""Relevance and document scan assessment models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import DomainModel, IdentifiedModel


class ParsedSourceMetadata(DomainModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    publisher_or_site: str | None = None
    published_at: datetime | None = None
    accessed_at: datetime | None = None
    doi: str | None = None
    isbn: str | None = None
    license: str | None = None
    language: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class RelevanceAssessment(IdentifiedModel):
    document_id: str | None = None
    search_hit_id: str | None = None
    assignment_id: str | None = None
    is_relevant: bool
    score: float | None = None
    rationale: str = ""
    suggested_use: str | None = None


class SegmentScanResult(IdentifiedModel):
    document_id: str
    document_version_id: str
    segment_id: str
    status: str = "completed"
    summary: str | None = None
    proposed_claim_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    batch_index: int = 0


class DocumentScanSummary(IdentifiedModel):
    document_id: str
    document_version_id: str
    total_segments: int = 0
    completed_segments: int = 0
    failed_segments: int = 0
    is_complete: bool = False
    warnings: list[str] = Field(default_factory=list)
