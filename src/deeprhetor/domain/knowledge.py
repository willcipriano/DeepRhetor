"""Claims, evidence, and verification models."""

from __future__ import annotations

import hashlib

from pydantic import Field

from .base import DomainModel, IdentifiedModel
from .enums import (
    ClaimStatus,
    EvidenceDirectness,
    EvidenceRelation,
    VerificationDecisionKind,
)


class EvidenceLocation(DomainModel):
    """Exact span within an archived document version."""

    char_start: int | None = None
    char_end: int | None = None
    page: int | None = None
    section_path: str | None = None


class Evidence(IdentifiedModel):
    """Verbatim quote or precisely identified source span."""

    document_id: str
    document_version_id: str
    document_segment_id: str | None = None
    quote: str
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    content_hash: str = ""

    def ensure_content_hash(self) -> Evidence:
        """Fill content_hash from quote when empty."""
        if self.content_hash:
            return self
        return self.model_copy(update={"content_hash": quote_content_hash(self.quote)})


def quote_content_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


class ClaimEvidenceLink(DomainModel):
    evidence_id: str
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS
    directness: EvidenceDirectness = EvidenceDirectness.DIRECT
    explanation: str = ""


class ProposedClaim(IdentifiedModel):
    statement: str
    status: ClaimStatus = ClaimStatus.PROPOSED
    project_id: str | None = None
    run_id: str | None = None
    topic_id: str | None = None
    assignment_id: str | None = None
    evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    worker_notes: str | None = None


class ClaimRelation(DomainModel):
    from_claim_id: str
    to_claim_id: str
    relation: str  # duplication | dependence | tension | contradiction
    notes: str | None = None


class VerificationDecision(IdentifiedModel):
    claim_id: str
    decision: VerificationDecisionKind
    notes: str | None = None
    corrected_statement: str | None = None
    evidence_ids_checked: list[str] = Field(default_factory=list)
    quote_check_passed: bool | None = None
    failures: list[str] = Field(default_factory=list)
