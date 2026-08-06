"""Workflow runtime context shared by graph nodes (not graph state)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.plugins.registry import SearchProviderRegistry, create_default_registry
from deeprhetor.repositories.operations import EventRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.project import ProjectRepository
from deeprhetor.repositories.workflow import RunRepository, TaskRepository
from deeprhetor.services.checkpoint import CheckpointStore
from deeprhetor.workflow.agents import (
    FakeSupervisor,
    FakeTopicWorker,
    SupervisorAgent,
    TopicWorkerAgent,
)


@dataclass
class WorkflowContext:
    """Repositories, providers, and agents closed over by graph nodes."""

    engine: AsyncEngine
    projects: ProjectRepository
    runs: RunRepository
    tasks: TaskRepository
    plans: ResearchPlanRepository
    events: EventRepository
    checkpoints: CheckpointStore
    providers: SearchProviderRegistry
    supervisor: SupervisorAgent
    worker: TopicWorkerAgent
    configuration_snapshot_id: str | None = None

    @classmethod
    def from_engine(
        cls,
        engine: AsyncEngine,
        *,
        supervisor: SupervisorAgent | None = None,
        worker: TopicWorkerAgent | None = None,
        providers: SearchProviderRegistry | None = None,
        configuration_snapshot_id: str | None = None,
    ) -> WorkflowContext:
        return cls(
            engine=engine,
            projects=ProjectRepository(engine),
            runs=RunRepository(engine),
            tasks=TaskRepository(engine),
            plans=ResearchPlanRepository(engine),
            events=EventRepository(engine),
            checkpoints=CheckpointStore(engine),
            providers=providers or create_default_registry(),
            supervisor=supervisor or FakeSupervisor(),
            worker=worker or FakeTopicWorker(),
            configuration_snapshot_id=configuration_snapshot_id,
        )
