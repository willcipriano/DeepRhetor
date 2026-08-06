"""Supervisor and topic-worker callables used by workflow nodes.

Production code may wrap Stage 4 Pydantic AI agents. Integration tests inject
FAKE agents so no OpenRouter calls are made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from deeprhetor.domain.enums import PlanStatus, RhetoricalPosture
from deeprhetor.domain.knowledge import (
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from deeprhetor.domain.planning import (
    CoverageReport,
    PlanSection,
    PlanTopic,
    ResearchPlan,
)
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.services.critic import CriticLoopState, CriticPassResult


class SupervisorAgent(Protocol):
    async def create_plan(
        self,
        *,
        project_id: str,
        prompt: str,
        feedback: str | None = None,
        version: int = 1,
    ) -> ResearchPlan: ...


class TopicWorkerAgent(Protocol):
    async def acknowledge(self, assignment: dict[str, Any]) -> dict[str, Any]: ...


class KnowledgeProposer(Protocol):
    async def propose_for_plan(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: ResearchPlan,
        documents: DocumentRepository,
        claims: ClaimRepository,
        evidence: EvidenceRepository,
    ) -> list[str]:
        """Return newly proposed claim IDs."""
        ...


class CoverageCriticAgent(Protocol):
    async def judge(
        self,
        *,
        plan: ResearchPlan,
        project_id: str,
        plan_id: str | None,
        run_id: str | None,
        state: CriticLoopState,
        critic_service: Any,
    ) -> CriticPassResult: ...


@dataclass
class FakeSupervisor:
    """Deterministic supervisor returning a structured ResearchPlan."""

    call_count: int = 0
    last_feedback: str | None = None
    topics: list[PlanTopic] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.topics:
            self.topics = [
                PlanTopic(
                    topic_id="topic-history",
                    title="Historical framing",
                    objective="Establish historical context",
                    research_angles=["origins and early usage"],
                    desired_source_classes=["encyclopedia"],
                ),
                PlanTopic(
                    topic_id="topic-evidence",
                    title="Primary evidence",
                    objective="Gather direct evidence",
                    research_angles=["primary sources"],
                    desired_source_classes=["encyclopedia", "scholarly"],
                ),
            ]

    async def create_plan(
        self,
        *,
        project_id: str,
        prompt: str,
        feedback: str | None = None,
        version: int = 1,
    ) -> ResearchPlan:
        self.call_count += 1
        self.last_feedback = feedback
        title_suffix = f" (rev {version})" if version > 1 else ""
        notes = []
        if feedback:
            notes.append(f"Addressed feedback: {feedback}")
        return ResearchPlan(
            id=str(uuid4()),
            project_id=project_id,
            prompt=prompt,
            rhetorical_posture=RhetoricalPosture.EXPLANATORY,
            status=PlanStatus.DRAFT,
            version=version,
            topics=list(self.topics),
            sections=[
                PlanSection(
                    section_id="sec-1",
                    title=f"Overview{title_suffix}",
                    questions=["What is known?"],
                    topic_ids=[t.topic_id for t in self.topics],
                    order=0,
                )
            ],
            inclusion_boundaries=["Stay within the prompt"],
            exclusion_boundaries=["Do not invent sources"],
            expected_evidence_classes=["direct", "testimony"],
            completion_criteria=["Each topic has at least one acknowledged worker"],
            worker_assignments=[],
            metadata={"fake_supervisor_calls": self.call_count, "notes": notes},
        )


@dataclass
class FakeTopicWorker:
    """Worker that only acknowledges assignments (no model / network)."""

    acknowledgements: list[dict[str, Any]] = field(default_factory=list)

    async def acknowledge(self, assignment: dict[str, Any]) -> dict[str, Any]:
        result = {
            "ok": True,
            "acknowledged": True,
            "topic_id": assignment.get("topic_id"),
            "provider": assignment.get("provider_or_class"),
        }
        self.acknowledgements.append(result)
        return result


@dataclass
class FakeKnowledgeProposer:
    """Propose one grounded claim per plan topic using archived segments when present."""

    proposed_ids: list[str] = field(default_factory=list)

    async def propose_for_plan(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: ResearchPlan,
        documents: DocumentRepository,
        claims: ClaimRepository,
        evidence: EvidenceRepository,
    ) -> list[str]:
        docs = await documents.list_for_project(project_id)
        claim_ids: list[str] = []
        for topic in plan.topics:
            statement = f"Claim covering {topic.title}"
            claim = ProposedClaim(
                statement=statement,
                topic_id=topic.topic_id,
                project_id=project_id,
                run_id=run_id,
            )
            stored = await claims.create(claim, project_id=project_id, run_id=run_id)
            if docs:
                version = await documents.latest_version(docs[0].id)
                if version is not None:
                    segs = await documents.list_segments(version.id)
                    if segs:
                        quote = segs[0].text[: min(80, len(segs[0].text))]
                        if quote:
                            ev = Evidence(
                                document_id=docs[0].id,
                                document_version_id=version.id,
                                document_segment_id=segs[0].id,
                                quote=quote,
                                location=EvidenceLocation(
                                    char_start=segs[0].char_start or 0,
                                    char_end=(segs[0].char_start or 0) + len(quote),
                                ),
                                content_hash=quote_content_hash(quote),
                            )
                            created = await evidence.create(ev)
                            await claims.attach_evidence(stored.id, created.id)
            claim_ids.append(stored.id)
        self.proposed_ids.extend(claim_ids)
        return claim_ids


@dataclass
class FakeCoverageCritic:
    """Test doubles for coverage judgment.

    ``force_complete=True`` (default) ends the Stage 5-compatible path without
    requiring a full claim inventory. Set False to use CoverageCriticService.
    """

    force_complete: bool = True
    judgments: list[CriticPassResult] = field(default_factory=list)

    async def judge(
        self,
        *,
        plan: ResearchPlan,
        project_id: str,
        plan_id: str | None,
        run_id: str | None,
        state: CriticLoopState,
        critic_service: Any,
    ) -> CriticPassResult:
        if self.force_complete:
            state.pass_count += 1
            state.is_complete = True
            state.terminated_reason = "complete"
            report = CoverageReport(
                plan_id=plan_id or plan.id,
                plan_version=plan.version,
                is_complete=True,
                covered_section_ids=[s.section_id for s in plan.sections],
                notes=["fake critic force_complete"],
            )
            state.last_report = report
            result = CriticPassResult(report=report, state=state, should_continue=False)
            self.judgments.append(result)
            return result
        result = await critic_service.evaluate(
            plan=plan,
            project_id=project_id,
            plan_id=plan_id,
            run_id=run_id,
            state=state,
        )
        self.judgments.append(result)
        return result


@dataclass
class PydanticSupervisor:
    """Adapter around a Stage 4 supervisor Agent (structured ResearchPlan output)."""

    agent: Any
    deps: Any

    async def create_plan(
        self,
        *,
        project_id: str,
        prompt: str,
        feedback: str | None = None,
        version: int = 1,
    ) -> ResearchPlan:
        message = prompt if not feedback else f"{prompt}\n\nFeedback:\n{feedback}"
        result = await self.agent.run(message, deps=self.deps)
        plan = result.output
        if not isinstance(plan, ResearchPlan):
            plan = ResearchPlan.model_validate(plan)
        return plan.model_copy(
            update={
                "project_id": project_id,
                "prompt": prompt,
                "version": version,
                "status": PlanStatus.DRAFT,
            }
        )
