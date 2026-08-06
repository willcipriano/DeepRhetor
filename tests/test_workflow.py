"""Stage 5 thin LangGraph workflow tests (fake agents — no OpenRouter)."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeprhetor.domain.enums import PlanStatus, RunStatus, TaskStatus
from deeprhetor.domain.sources import ProviderDescriptor
from deeprhetor.plugins.registry import SearchProviderRegistry
from deeprhetor.repositories.operations import EventRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.workflow import TaskRepository
from deeprhetor.services.project_store import create_project_async
from deeprhetor.workflow import (
    FakeSupervisor,
    FakeTopicWorker,
    ProjectSqliteSaver,
    build_capability_aware_assignments,
    matching_providers_for_topic,
    open_workflow,
    resume_with_approval,
    start_until_plan_interrupt,
)
from deeprhetor.workflow.dispatch import assignment_idempotency_key
from deeprhetor.domain.planning import PlanTopic, ResearchPlan


class _StubProvider:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    async def search(self, request):  # pragma: no cover - unused in Stage 5
        raise NotImplementedError


def _registry_with_mediawiki_and_scholarly() -> SearchProviderRegistry:
    registry = SearchProviderRegistry()
    registry.register(
        _StubProvider(
            ProviderDescriptor(
                name="mediawiki",
                version="1.0.0",
                source_classes=["encyclopedia", "web"],
            )
        )
    )
    registry.register(
        _StubProvider(
            ProviderDescriptor(
                name="openalex",
                version="1.0.0",
                source_classes=["scholarly"],
            )
        )
    )
    return registry


def _has_interrupt(chunks: list) -> bool:
    for chunk in chunks:
        if isinstance(chunk, dict) and "__interrupt__" in chunk:
            return True
        if isinstance(chunk, tuple) and chunk and chunk[0] == "__interrupt__":
            return True
    return False


@pytest.mark.asyncio
async def test_capability_aware_dispatch_not_cartesian() -> None:
    plan = ResearchPlan(
        project_id="p",
        prompt="prompt",
        topics=[
            PlanTopic(
                topic_id="t1",
                title="History",
                objective="history",
                desired_source_classes=["encyclopedia"],
            ),
            PlanTopic(
                topic_id="t2",
                title="Papers",
                objective="papers",
                desired_source_classes=["scholarly"],
            ),
        ],
    )
    descriptors = [
        ProviderDescriptor(name="mediawiki", version="1", source_classes=["encyclopedia"]),
        ProviderDescriptor(name="openalex", version="1", source_classes=["scholarly"]),
        ProviderDescriptor(name="tavily", version="1", source_classes=["web"]),
    ]
    # Encyclopedia topic must not receive openalex or tavily.
    hist = matching_providers_for_topic(plan.topics[0], descriptors)
    assert [d.name for d in hist] == ["mediawiki"]

    assignments = build_capability_aware_assignments(plan, descriptors)
    pairs = {(a.topic_id, a.provider_or_class) for a in assignments}
    assert pairs == {("t1", "mediawiki"), ("t2", "openalex")}
    # Cartesian of 2 topics × 3 providers would be 6; we expect 2.
    assert len(assignments) == 2


@pytest.mark.asyncio
async def test_plan_interrupt_feedback_then_approve_fanout(tmp_path: Path) -> None:
    path = tmp_path / "wf.deeprhetor"
    opened = await create_project_async(path, title="WF", prompt="Explain rhetoric")
    supervisor = FakeSupervisor()
    worker = FakeTopicWorker()
    handle = await open_workflow(
        opened.engine,
        project_id=opened.project.id,
        configuration_snapshot_id=(
            opened.configuration_snapshot.id if opened.configuration_snapshot else None
        ),
        supervisor=supervisor,
        worker=worker,
        providers=_registry_with_mediawiki_and_scholarly(),
    )

    chunks = await start_until_plan_interrupt(handle)
    assert _has_interrupt(chunks)
    assert supervisor.call_count == 1

    plans = ResearchPlanRepository(opened.engine)
    draft = await plans.latest_for_run(handle.run.id)
    assert draft is not None
    assert draft.status == PlanStatus.AWAITING_APPROVAL
    assert draft.version == 1

    # Feedback loop → revised plan.
    chunks_fb = await resume_with_approval(
        handle, action="feedback", feedback="Narrow the history topic"
    )
    assert _has_interrupt(chunks_fb)
    assert supervisor.call_count == 2
    assert supervisor.last_feedback == "Narrow the history topic"
    revised = await plans.latest_for_run(handle.run.id)
    assert revised is not None
    assert revised.version == 2
    assert revised.status == PlanStatus.AWAITING_APPROVAL
    superseded = await plans.get_by_project_version(opened.project.id, 1)
    assert superseded is not None
    assert superseded.status == PlanStatus.SUPERSEDED

    # Approve → fan-out → join.
    chunks_ok = await resume_with_approval(handle, action="approve")
    assert not _has_interrupt(chunks_ok)

    approved = await plans.get(revised.id)
    assert approved is not None
    assert approved.status == PlanStatus.APPROVED
    assert approved.approved_at is not None

    tasks = TaskRepository(opened.engine)
    listed = await tasks.list_for_run(handle.run.id)
    worker_tasks = [t for t in listed if t.kind == "topic_worker"]
    assert len(worker_tasks) >= 1
    assert all(t.status == TaskStatus.COMPLETED for t in worker_tasks)
    assert worker.acknowledgements

    events = EventRepository(opened.engine)
    kinds = {e.kind for e in await events.list_for_run(handle.run.id)}
    assert "workflow.plan_created" in kinds
    assert "workflow.plan_approved" in kinds
    assert "workflow.dispatched" in kinds
    assert "workflow.progress" in kinds
    assert "workflow.completed" in kinds

    run = await handle.ctx.runs.get(handle.run.id)
    assert run is not None
    assert run.status == RunStatus.COMPLETED
    assert run.plan_version == 2
    await opened.dispose()


@pytest.mark.asyncio
async def test_checkpoint_kill_resume_no_duplicate_tasks(tmp_path: Path) -> None:
    path = tmp_path / "resume.deeprhetor"
    opened = await create_project_async(path, title="R", prompt="Resume test")
    supervisor = FakeSupervisor()
    worker = FakeTopicWorker()
    providers = _registry_with_mediawiki_and_scholarly()
    snapshot_id = (
        opened.configuration_snapshot.id if opened.configuration_snapshot else None
    )

    handle = await open_workflow(
        opened.engine,
        project_id=opened.project.id,
        configuration_snapshot_id=snapshot_id,
        supervisor=supervisor,
        worker=worker,
        providers=providers,
    )
    await start_until_plan_interrupt(handle)

    # Simulate process kill while awaiting approval: new graph + saver from SQLite.
    from deeprhetor.workflow.graph import build_workflow_graph

    resumed_ctx = handle.ctx
    fresh_saver = ProjectSqliteSaver(opened.engine, run_id=handle.run.id)
    resumed_graph = build_workflow_graph(resumed_ctx).compile(checkpointer=fresh_saver)
    from deeprhetor.workflow.runtime import WorkflowHandle, resume_with_approval

    resumed = WorkflowHandle(
        ctx=resumed_ctx,
        checkpointer=fresh_saver,
        graph=resumed_graph,
        run=handle.run,
    )

    state = await resumed.get_state()
    assert "human_plan_approval" in (state.next or ())

    await resume_with_approval(resumed, action="approve")

    tasks = TaskRepository(opened.engine)
    first = await tasks.list_for_run(handle.run.id)
    worker_tasks = [t for t in first if t.kind == "topic_worker"]
    assert worker_tasks
    first_ids = {t.id for t in worker_tasks}
    first_keys = {t.idempotency_key for t in worker_tasks}

    # Replay-safe task creation after another simulated resume.
    plans = ResearchPlanRepository(opened.engine)
    stored = await plans.latest_for_run(handle.run.id)
    assert stored is not None
    assignments = build_capability_aware_assignments(
        stored.plan, providers.descriptors()
    )
    for assignment in assignments:
        key = assignment_idempotency_key(
            run_id=handle.run.id,
            plan_version=stored.version,
            topic_id=assignment.topic_id,
            provider=assignment.provider_or_class,
        )
        await tasks.create(
            run_id=handle.run.id,
            kind="topic_worker",
            assignment=assignment.model_dump(mode="json"),
            idempotency_key=key,
        )

    second = await tasks.list_for_run(handle.run.id)
    worker_tasks2 = [t for t in second if t.kind == "topic_worker"]
    assert {t.id for t in worker_tasks2} == first_ids
    assert {t.idempotency_key for t in worker_tasks2} == first_keys
    assert len(worker_tasks2) == len(worker_tasks)

    node_cps = await resumed.ctx.checkpoints.list_for_run(handle.run.id, namespace="nodes")
    assert any(c.node_name == "dispatch_workers" for c in node_cps)
    assert any(c.node_name == "join_and_progress" for c in node_cps)

    await opened.dispose()


@pytest.mark.asyncio
async def test_graph_state_excludes_corpus_fields() -> None:
    from deeprhetor.workflow.state import WorkflowState

    annotations = set(WorkflowState.__annotations__)
    forbidden = {
        "documents",
        "claims",
        "corpus",
        "plan",
        "research_plan",
        "segments",
        "evidence",
    }
    assert annotations.isdisjoint(forbidden)
    assert {
        "project_id",
        "run_id",
        "plan_version",
        "task_ids",
        "stage",
    } <= annotations
