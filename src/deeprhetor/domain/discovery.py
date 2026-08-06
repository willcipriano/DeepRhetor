"""Search and discovery models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import DomainModel, IdentifiedModel


class SearchRequest(DomainModel):
    query: str
    provider: str
    source_classes: list[str] = Field(default_factory=list)
    max_results: int = 10
    language: str | None = None
    freshness_days: int | None = None
    assignment_id: str | None = None
    idempotency_key: str | None = None


class SearchHit(DomainModel):
    hit_id: str
    title: str
    url: str | None = None
    snippet: str | None = None
    score: float | None = None
    published_at: datetime | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(IdentifiedModel):
    request: SearchRequest
    hits: list[SearchHit] = Field(default_factory=list)
    provider: str
    raw_ref: str | None = None
