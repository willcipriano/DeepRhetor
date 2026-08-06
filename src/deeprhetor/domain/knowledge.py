"""Claims, evidence, and verification models."""

from __future__ import annotations

from pydantic import Field

from .base import DomainModel, IdentifiedModel
from .enums import (
    ClaimStatus,
    EvidenceDirectness,
    EvidenceRelation,
    VerificationDecisionKind,
)


class ClaimEvidenceLink(DomainModel):
    evidence_id: str
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS
    directness: EvidenceDirectness = EvidenceDirectness.DIRECT
    explanation: str = ""


class ProposedClaim(IdentifiedModel):
    statement: str
    status: ClaimStatus = ClaimStatus.PROPOSED
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
