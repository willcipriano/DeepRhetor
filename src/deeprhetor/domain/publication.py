"""Validation and publication result models."""

from __future__ import annotations

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


class PublicationResult(IdentifiedModel):
    draft_id: str
    validation_id: str | None = None
    status: PublicationStatus = PublicationStatus.PENDING
    pdf_artifact_id: str | None = None
    tex_artifact_id: str | None = None
    bib_artifact_id: str | None = None
    manifest_artifact_id: str | None = None
    error_message: str | None = None
