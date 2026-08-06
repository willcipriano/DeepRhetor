"""Supervisor and topic-worker callables used by workflow nodes.

Production code may wrap Stage 4 Pydantic AI agents. Integration tests inject
FAKE agents so no OpenRouter calls are made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from deeprhetor.domain.enums import PlanStatus, RhetoricalPosture
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan, WorkerAssignment


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
