"""Shared domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    CREATED = "created"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    NEEDS_CORRECTION = "needs_correction"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    CONTRADICTS = "contradicts"


class EvidenceDirectness(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    TESTIMONY = "testimony"
    FOLKLORE = "folklore"
    INFERENCE = "inference"


class RhetoricalPosture(StrEnum):
    EXPLANATORY = "explanatory"
    NEUTRAL = "neutral"
    ARGUMENTATIVE = "argumentative"


class VerificationDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CORRECTION = "request_correction"


class ValidationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNINGS = "warnings"


class PublicationStatus(StrEnum):
    PENDING = "pending"
    RENDERED = "rendered"
    COMPILED = "compiled"
    FAILED = "failed"
