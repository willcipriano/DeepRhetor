"""Coverage critic: compare approved claims to plan; emit gaps for supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.config.settings import LimitsConfig
from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.planning import (
    CoverageGapRequest,
    CoverageReport,
    ResearchPlan,
)
from deeprhetor.repositories.knowledge import ClaimRepository, StoredClaim
from deeprhetor.repositories.base import utcnow
from deeprhetor.repositories.scan import ScanRepository


@dataclass
class CriticLoopState:
    """Mutable critic loop control — never dispatches workers itself."""

    pass_count: int = 0
    started_at: datetime = field(default_factory=utcnow)
    last_report: CoverageReport | None = None
    gap_requests: list[CoverageGapRequest] = field(default_factory=list)
    is_complete: bool = False
    terminated_reason: str | None = None

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        return ((now or utcnow()) - self.started_at).total_seconds()


@dataclass
class CriticPassResult:
    report: CoverageReport
    state: CriticLoopState
    should_continue: bool
    """True when gaps exist and limits allow another research cycle."""


class CoverageCriticService:
    """Mid-tier coverage critic — emits gap requests; never dispatches workers."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        limits: LimitsConfig | None = None,
        claims: ClaimRepository | None = None,
        scans: ScanRepository | None = None,
    ) -> None:
        self._engine = engine
        self.limits = limits or LimitsConfig()
        self.claims = claims or ClaimRepository(engine)
        self.scans = scans or ScanRepository(engine)

    def limits_exceeded(self, state: CriticLoopState, *, now: datetime | None = None) -> str | None:
        if state.pass_count >= self.limits.max_critic_passes:
            return "max_critic_passes"
        if state.elapsed_seconds(now) >= self.limits.max_run_duration_seconds:
            return "max_run_duration"
        return None

    async def evaluate(
        self,
        *,
        plan: ResearchPlan,
        project_id: str,
        plan_id: str | None = None,
        run_id: str | None = None,
        state: CriticLoopState | None = None,
    ) -> CriticPassResult:
        loop = state or CriticLoopState()
        if loop.elapsed_seconds() >= self.limits.max_run_duration_seconds:
            loop.terminated_reason = "max_run_duration"
            empty = CoverageReport(
                plan_id=plan_id or plan.id,
                plan_version=plan.version,
                is_complete=False,
                notes=["max run duration exceeded before pass"],
            )
            loop.last_report = empty
            return CriticPassResult(report=empty, state=loop, should_continue=False)

        approved = await self.claims.list_for_project(
            project_id, status=ClaimStatus.APPROVED, run_id=run_id
        )
        incomplete_scans = await self.scans.list_incomplete_document_scans()
        report = self._build_report(
            plan=plan,
            plan_id=plan_id or plan.id,
            approved=approved,
            unscanned_ids=[s.document_version_id for s in incomplete_scans],
        )
        loop.pass_count += 1
        loop.last_report = report
        if report.gaps:
            loop.gap_requests.extend(report.gaps)

        if report.is_complete:
            loop.is_complete = True
            loop.terminated_reason = "complete"
            return CriticPassResult(report=report, state=loop, should_continue=False)

        if loop.pass_count >= self.limits.max_critic_passes:
            loop.terminated_reason = "max_critic_passes"
            return CriticPassResult(report=report, state=loop, should_continue=False)

        if loop.elapsed_seconds() >= self.limits.max_run_duration_seconds:
            loop.terminated_reason = "max_run_duration"
            return CriticPassResult(report=report, state=loop, should_continue=False)

        if report.gaps or incomplete_scans:
            return CriticPassResult(report=report, state=loop, should_continue=True)

        loop.is_complete = True
        loop.terminated_reason = "complete"
        return CriticPassResult(report=report, state=loop, should_continue=False)

    def _build_report(
        self,
        *,
        plan: ResearchPlan,
        plan_id: str,
        approved: list[StoredClaim],
        unscanned_ids: list[str],
    ) -> CoverageReport:
        claims_by_topic: dict[str | None, list[StoredClaim]] = {}
        for claim in approved:
            claims_by_topic.setdefault(claim.topic_id, []).append(claim)

        covered_sections: list[str] = []
        uncovered_sections: list[str] = []
        gaps: list[CoverageGapRequest] = []

        for section in plan.sections:
            topic_ids = section.topic_ids or []
            has_claim = False
            for tid in topic_ids:
                if claims_by_topic.get(tid):
                    has_claim = True
                    break
            # Sections with no topic linkage: covered if any approved claim exists.
            if not topic_ids and approved:
                has_claim = True
            if has_claim:
                covered_sections.append(section.section_id)
            else:
                uncovered_sections.append(section.section_id)
                gaps.append(
                    CoverageGapRequest(
                        gap_id=str(uuid4()),
                        description=f"Section '{section.title}' lacks approved claims",
                        related_section_ids=[section.section_id],
                        related_topic_ids=list(topic_ids),
                        suggested_providers=[],
                        priority=1,
                    )
                )

        covered_topics = {c.topic_id for c in approved if c.topic_id}
        for topic in plan.topics:
            if topic.topic_id not in covered_topics:
                # Avoid duplicate gaps when section already flagged the topic.
                already = any(topic.topic_id in g.related_topic_ids for g in gaps)
                if not already:
                    gaps.append(
                        CoverageGapRequest(
                            gap_id=str(uuid4()),
                            description=f"Topic '{topic.title}' lacks approved claims",
                            related_section_ids=[],
                            related_topic_ids=[topic.topic_id],
                            suggested_providers=list(topic.desired_source_classes),
                            priority=2,
                        )
                    )

        unsupported: list[str] = []
        for claim in approved:
            if not claim.claim.evidence_links:
                unsupported.append(claim.id)

        # Duplicate detection by normalized statement.
        seen: dict[str, list[str]] = {}
        for claim in approved:
            key = claim.statement.strip().lower()
            seen.setdefault(key, []).append(claim.id)
        duplicates = [cid for ids in seen.values() if len(ids) > 1 for cid in ids]

        if unscanned_ids:
            gaps.append(
                CoverageGapRequest(
                    gap_id=str(uuid4()),
                    description="Unresolved document scans remain",
                    related_section_ids=[],
                    related_topic_ids=[],
                    suggested_providers=[],
                    priority=0,
                )
            )

        topics_covered = (
            all(topic.topic_id in covered_topics for topic in plan.topics)
            if plan.topics
            else bool(approved)
        )
        is_complete = (
            len(approved) > 0
            and not uncovered_sections
            and not unscanned_ids
            and topics_covered
        )

        notes: list[str] = []
        if not approved:
            notes.append("No approved claims yet")
        if unscanned_ids:
            notes.append(f"{len(unscanned_ids)} incomplete document scans")

        return CoverageReport(
            plan_id=plan_id,
            plan_version=plan.version,
            is_complete=is_complete,
            covered_section_ids=covered_sections,
            uncovered_section_ids=uncovered_sections,
            unsupported_claim_ids=unsupported,
            duplicate_claim_ids=duplicates,
            unscanned_document_ids=unscanned_ids,
            notes=notes,
            gaps=[] if is_complete else gaps,
        )

    def gap_requests_as_dicts(self, gaps: list[CoverageGapRequest]) -> list[dict[str, Any]]:
        return [g.model_dump(mode="json") for g in gaps]
