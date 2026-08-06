"""Validation and publication result models."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import DomainModel, IdentifiedModel
from .enums import PublicationStatus, ValidationOutcome


class ValidationIssue(DomainModel):
    code: str
    message: str
    path: str | None = None
    severity: str = "error"


class ValidationResult(IdentifiedModel):
    draft_id: str
    outcome: ValidationOutcome
    issues: list[ValidationIssue] = Field(default_factory=list)


class ProvenanceManifest(IdentifiedModel):
    """Machine-readable provenance for a published draft."""

    project_id: str
    draft_id: str
    outline_id: str | None = None
    plan_id: str | None = None
    validation_id: str | None = None
    title: str = ""
    citation_keys: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    document_content_hashes: dict[str, str] = Field(default_factory=dict)
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    toolchain: dict[str, Any] = Field(default_factory=dict)
    validation_outcome: ValidationOutcome | None = None


class PublicationResult(IdentifiedModel):
    draft_id: str
    validation_id: str | None = None
    status: PublicationStatus = PublicationStatus.PENDING
    pdf_artifact_id: str | None = None
    tex_artifact_id: str | None = None
    bib_artifact_id: str | None = None
    manifest_artifact_id: str | None = None
    validation_report_artifact_id: str | None = None
    error_message: str | None = None
    pdf_compiled: bool = False
    pdf_skipped_reason: str | None = None
    manifest: ProvenanceManifest | None = None
    tex: str | None = None
    bib: str | None = None
