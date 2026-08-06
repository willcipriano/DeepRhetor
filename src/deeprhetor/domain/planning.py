"""Research planning and coverage models."""

from __future__ import annotations

from pydantic import Field

from .base import DomainModel, IdentifiedModel
from .enums import PlanStatus, RhetoricalPosture, TaskStatus


class PlanTopic(DomainModel):
    topic_id: str
    title: str
    objective: str
    research_angles: list[str] = Field(default_factory=list)
    desired_source_classes: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class PlanSection(DomainModel):
    section_id: str
    title: str
    questions: list[str] = Field(default_factory=list)
    topic_ids: list[str] = Field(default_factory=list)
    order: int = 0


class WorkerAssignment(IdentifiedModel):
    topic_id: str
    objective: str
    provider_or_class: str
    allowed_tools: list[str] = Field(default_factory=list)
    expected_output_schema: str = "ProposedClaim"
    acceptance_criteria: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    max_searches: int = 10
    max_retries: int = 3
    max_iterations: int = 5
    dependency_ids: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


class ResearchPlan(IdentifiedModel):
    project_id: str
    prompt: str
    rhetorical_posture: RhetoricalPosture = RhetoricalPosture.EXPLANATORY
    status: PlanStatus = PlanStatus.DRAFT
    version: int = 1
    topics: list[PlanTopic] = Field(default_factory=list)
    sections: list[PlanSection] = Field(default_factory=list)
    inclusion_boundaries: list[str] = Field(default_factory=list)
    exclusion_boundaries: list[str] = Field(default_factory=list)
    expected_evidence_classes: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    worker_assignments: list[WorkerAssignment] = Field(default_factory=list)


class CoverageGapRequest(DomainModel):
    gap_id: str
    description: str
    related_section_ids: list[str] = Field(default_factory=list)
    related_topic_ids: list[str] = Field(default_factory=list)
    suggested_providers: list[str] = Field(default_factory=list)
    priority: int = 0


class CoverageReport(IdentifiedModel):
    plan_id: str
    plan_version: int
    is_complete: bool = False
    covered_section_ids: list[str] = Field(default_factory=list)
    uncovered_section_ids: list[str] = Field(default_factory=list)
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    duplicate_claim_ids: list[str] = Field(default_factory=list)
    unscanned_document_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    gaps: list[CoverageGapRequest] = Field(default_factory=list)
