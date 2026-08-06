"""Typed repository package exports."""

from __future__ import annotations

from .base import BaseRepository
from .document import Document, DocumentRepository, DocumentSegment, DocumentVersion
from .knowledge import (
    CLAIM_TRANSITIONS,
    ClaimRepository,
    ClaimTransitionError,
    EvidenceRepository,
    StoredClaim,
)
from .operations import (
    Artifact,
    ArtifactRepository,
    ErrorRecord,
    ErrorRepository,
    Event,
    EventRepository,
    ModelCall,
    ModelCallRepository,
    UsageRecord,
    UsageRecordRepository,
)
from .planning import ResearchPlanRepository, StoredResearchPlan
from .project import ConfigurationSnapshot, Project, ProjectRepository
from .scan import (
    TERMINAL_SCAN_STATUSES,
    DocumentScanRecord,
    ScanRepository,
    SegmentScanRecord,
)
from .workflow import Run, RunRepository, Task, TaskRepository
from .writing import (
    CitationKeyRepository,
    DraftRepository,
    OutlineRepository,
    StoredDraft,
    StoredOutline,
    ValidationResultRepository,
)

__all__ = [
    "CLAIM_TRANSITIONS",
    "Artifact",
    "ArtifactRepository",
    "BaseRepository",
    "CitationKeyRepository",
    "ClaimRepository",
    "ClaimTransitionError",
    "ConfigurationSnapshot",
    "Document",
    "DocumentRepository",
    "DocumentScanRecord",
    "DocumentSegment",
    "DocumentVersion",
    "DraftRepository",
    "ErrorRecord",
    "ErrorRepository",
    "Event",
    "EventRepository",
    "EvidenceRepository",
    "ModelCall",
    "ModelCallRepository",
    "OutlineRepository",
    "Project",
    "ProjectRepository",
    "ResearchPlanRepository",
    "Run",
    "RunRepository",
    "ScanRepository",
    "SegmentScanRecord",
    "StoredClaim",
    "StoredDraft",
    "StoredOutline",
    "StoredResearchPlan",
    "TERMINAL_SCAN_STATUSES",
    "Task",
    "TaskRepository",
    "UsageRecord",
    "UsageRecordRepository",
    "ValidationResultRepository",
]
