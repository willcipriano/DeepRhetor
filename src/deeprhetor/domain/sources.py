"""Fetch, parse, and provider-descriptor models for the source pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .assessment import ParsedSourceMetadata
from .base import DomainModel, IdentifiedModel, utcnow


class ProviderDescriptor(DomainModel):
    """Capability declaration for a search or source provider."""

    name: str
    version: str
    source_classes: list[str] = Field(default_factory=list)
    supports_freshness: bool = False
    supports_date_filter: bool = False
    languages: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    requires_auth: bool = False
    rate_limit_per_minute: int | None = None
    max_results: int | None = None
    returns: Literal["full_text", "metadata", "references"] = "references"
    licensing_notes: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class FetchRequest(DomainModel):
    url: str
    max_bytes: int | None = None
    timeout_seconds: float | None = None
    allowed_media_types: list[str] | None = None
    follow_redirects: bool = True
    idempotency_key: str | None = None


class FetchResult(DomainModel):
    original_url: str
    final_url: str
    media_type: str
    content: bytes
    headers: dict[str, str] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=utcnow)
    byte_size: int = 0
    sha256: str = ""
    status_code: int = 200


class RawDocument(DomainModel):
    content: bytes
    media_type: str
    filename: str | None = None
    source_url: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedSegment(DomainModel):
    text: str
    page: int | None = None
    section_path: str | None = None
    char_start: int
    char_end: int
    status: str = "pending"


class ParsedDocument(IdentifiedModel):
    media_type: str
    title: str | None = None
    text: str = ""
    segments: list[ParsedSegment] = Field(default_factory=list)
    parser: str
    parser_version: str
    source_metadata: ParsedSourceMetadata | None = None
