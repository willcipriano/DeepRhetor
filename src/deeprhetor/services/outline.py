"""Deterministic outline builder from approved plan + claims."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.planning import ResearchPlan
from deeprhetor.domain.writing import Outline, OutlineSection
from deeprhetor.repositories.knowledge import ClaimRepository, StoredClaim
from deeprhetor.repositories.writing import OutlineRepository, StoredOutline


class OutlineBuilderService:
    """Consolidate an approved plan and approved claims into a writing outline."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        claims: ClaimRepository | None = None,
        outlines: OutlineRepository | None = None,
    ) -> None:
        self._engine = engine
        self.claims = claims or ClaimRepository(engine)
        self.outlines = outlines or OutlineRepository(engine)

    def build_outline(
        self,
        plan: ResearchPlan,
        approved_claims: list[StoredClaim] | list[dict],
        *,
        title: str | None = None,
    ) -> Outline:
        """Deterministic outline: one section per plan section, claims assigned by topic."""
        claims = [_as_claim(c) for c in approved_claims]
        claims_by_topic: dict[str | None, list[str]] = {}
        unassigned: list[str] = []
        for claim in claims:
            if claim.status != ClaimStatus.APPROVED:
                continue
            topic = claim.topic_id
            if topic:
                claims_by_topic.setdefault(topic, []).append(claim.id)
            else:
                unassigned.append(claim.id)

        sections: list[OutlineSection] = []
        used: set[str] = set()
        plan_sections = sorted(plan.sections, key=lambda s: s.order)
        if not plan_sections:
            # Fall back to one section per topic.
            for idx, topic in enumerate(plan.topics):
                cids = list(claims_by_topic.get(topic.topic_id, []))
                used.update(cids)
                sections.append(
                    OutlineSection(
                        section_id=f"sec_{topic.topic_id}",
                        title=topic.title,
                        claim_ids=cids,
                        notes=topic.objective,
                        order=idx,
                    )
                )
        else:
            for section in plan_sections:
                cids: list[str] = []
                for tid in section.topic_ids:
                    for cid in claims_by_topic.get(tid, []):
                        if cid not in used:
                            cids.append(cid)
                            used.add(cid)
                sections.append(
                    OutlineSection(
                        section_id=section.section_id,
                        title=section.title,
                        claim_ids=cids,
                        notes="; ".join(section.questions) if section.questions else None,
                        order=section.order,
                    )
                )

        leftover = [cid for cid in unassigned if cid not in used]
        leftover.extend(
            cid
            for topic_claims in claims_by_topic.values()
            for cid in topic_claims
            if cid not in used
        )
        if leftover:
            sections.append(
                OutlineSection(
                    section_id="sec_additional_evidence",
                    title="Additional Evidence",
                    claim_ids=leftover,
                    order=len(sections),
                )
            )

        # Required scholarly framing sections when missing.
        titles_lower = {s.title.strip().lower() for s in sections}
        if "introduction" not in titles_lower:
            sections.insert(
                0,
                OutlineSection(
                    section_id="sec_introduction",
                    title="Introduction",
                    claim_ids=[],
                    order=-1,
                    notes="Framing section required for publication.",
                ),
            )
        if "conclusion" not in titles_lower:
            sections.append(
                OutlineSection(
                    section_id="sec_conclusion",
                    title="Conclusion",
                    claim_ids=[],
                    order=10_000,
                    notes="Closing section required for publication.",
                )
            )
        for idx, section in enumerate(sections):
            section.order = idx

        return Outline(
            plan_id=plan.id,
            plan_version=plan.version,
            title=title or plan.prompt[:120] or "Research Report",
            sections=sections,
        )

    async def build_and_persist(
        self,
        *,
        project_id: str,
        plan: ResearchPlan,
        title: str | None = None,
    ) -> StoredOutline:
        approved = await self.claims.list_by_status(project_id, [ClaimStatus.APPROVED])
        outline = self.build_outline(plan, approved, title=title)
        return await self.outlines.create(outline, project_id=project_id, plan_id=plan.id)


def _as_claim(value: StoredClaim | dict) -> StoredClaim:
    if isinstance(value, StoredClaim):
        return value
    return StoredClaim.model_validate(value)
