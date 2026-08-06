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
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    VerificationDecision,
    quote_content_hash,
)
from .planning import (
    CoverageGapRequest,
    CoverageReport,
    ResearchPlan,
    WorkerAssignment,
)
from .discovery import SearchHit, SearchRequest, SearchResponse
from .publication import (
    ProvenanceManifest,
    PublicationResult,
    ValidationIssue,
    ValidationResult,
)
from .sources import (
    FetchRequest,
    FetchResult,
    ParsedDocument,
    ParsedSegment,
    ProviderDescriptor,
    RawDocument,
)
from .writing import (
    BibEntry,
    CitationKey,
    DraftSection,
    Outline,
    OutlineSection,
    StructuredDraft,
)

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "BibEntry",
    "CitationKey",
    "ClaimEvidenceLink",
    "ClaimRelation",
    "ClaimStatus",
    "CoverageGapRequest",
    "CoverageReport",
    "DocumentScanSummary",
    "DraftSection",
    "Evidence",
    "EvidenceDirectness",
    "EvidenceLocation",
    "EvidenceRelation",
    "FetchRequest",
    "FetchResult",
    "Outline",
    "OutlineSection",
    "ParsedDocument",
    "ParsedSegment",
    "ParsedSourceMetadata",
    "PlanStatus",
    "ProposedClaim",
    "ProviderDescriptor",
    "ProvenanceManifest",
    "PublicationResult",
    "PublicationStatus",
    "RawDocument",
    "RelevanceAssessment",
    "ResearchPlan",
    "quote_content_hash",
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
