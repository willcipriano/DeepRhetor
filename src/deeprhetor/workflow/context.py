"""Workflow runtime context shared by graph nodes (not graph state)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.config.settings import LimitsConfig
from deeprhetor.plugins.registry import SearchProviderRegistry, create_default_registry
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.repositories.operations import EventRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.project import ProjectRepository
from deeprhetor.repositories.scan import ScanRepository
from deeprhetor.repositories.workflow import RunRepository, TaskRepository
from deeprhetor.services.checkpoint import CheckpointStore
from deeprhetor.services.critic import CoverageCriticService, CriticLoopState
from deeprhetor.services.scan import ScanService
from deeprhetor.services.verify import VerifierService
from deeprhetor.workflow.agents import (
    CoverageCriticAgent,
    FakeCoverageCritic,
    FakeSupervisor,
    FakeTopicWorker,
    KnowledgeProposer,
    FakeKnowledgeProposer,
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
    documents: DocumentRepository
    claims: ClaimRepository
    evidence: EvidenceRepository
    scans: ScanRepository
    scan_service: ScanService
    verifier: VerifierService
    critic_service: CoverageCriticService
    critic: CoverageCriticAgent
    proposer: KnowledgeProposer
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    configuration_snapshot_id: str | None = None
    critic_loop: CriticLoopState = field(default_factory=CriticLoopState)

    @classmethod
    def from_engine(
        cls,
        engine: AsyncEngine,
        *,
        supervisor: SupervisorAgent | None = None,
        worker: TopicWorkerAgent | None = None,
        critic: CoverageCriticAgent | None = None,
        proposer: KnowledgeProposer | None = None,
        providers: SearchProviderRegistry | None = None,
        limits: LimitsConfig | None = None,
        configuration_snapshot_id: str | None = None,
    ) -> WorkflowContext:
        lim = limits or LimitsConfig()
        documents = DocumentRepository(engine)
        claims = ClaimRepository(engine)
        evidence = EvidenceRepository(engine)
        scans = ScanRepository(engine)
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
            documents=documents,
            claims=claims,
            evidence=evidence,
            scans=scans,
            scan_service=ScanService(engine, documents=documents, scans=scans, batch_size=5),
            verifier=VerifierService(
                engine, claims=claims, evidence=evidence, documents=documents
            ),
            critic_service=CoverageCriticService(
                engine, limits=lim, claims=claims, scans=scans
            ),
            critic=critic or FakeCoverageCritic(force_complete=True),
            proposer=proposer or FakeKnowledgeProposer(),
            limits=lim,
            configuration_snapshot_id=configuration_snapshot_id,
        )
