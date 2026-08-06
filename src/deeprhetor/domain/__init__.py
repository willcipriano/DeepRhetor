"""Versioned domain models for structured model I/O and repository boundaries."""

from __future__ import annotations

from .assessment import (
    DocumentScanSummary,
    ParsedSourceMetadata,
    RelevanceAssessment,
    SegmentScanResult,
)
from .enums import (
    ClaimStatus,
    EvidenceDirectness,
    EvidenceRelation,
    PlanStatus,
    PublicationStatus,
    RhetoricalPosture,
    RunStatus,
    TaskStatus,
    ValidationOutcome,
    VerificationDecisionKind,
)
from .knowledge import (
    ClaimEvidenceLink,
    ClaimRelation,
    ProposedClaim,
    VerificationDecision,
)
from .planning import (
    CoverageGapRequest,
    CoverageReport,
    ResearchPlan,
    WorkerAssignment,
)
from .discovery import SearchHit, SearchRequest, SearchResponse
from .publication import PublicationResult, ValidationIssue, ValidationResult
from .writing import DraftSection, Outline, OutlineSection, StructuredDraft

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "ClaimEvidenceLink",
    "ClaimRelation",
    "ClaimStatus",
    "CoverageGapRequest",
    "CoverageReport",
    "DocumentScanSummary",
    "DraftSection",
    "EvidenceDirectness",
    "EvidenceRelation",
    "Outline",
    "OutlineSection",
    "ParsedSourceMetadata",
    "PlanStatus",
    "ProposedClaim",
    "PublicationResult",
    "PublicationStatus",
    "RelevanceAssessment",
    "ResearchPlan",
    "RhetoricalPosture",
    "RunStatus",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "SegmentScanResult",
    "StructuredDraft",
    "TaskStatus",
    "ValidationIssue",
    "ValidationOutcome",
    "ValidationResult",
    "VerificationDecision",
    "VerificationDecisionKind",
    "WorkerAssignment",
]
