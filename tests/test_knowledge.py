"""Stage 7: scan accounting, claims/evidence, verifier, critic loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeprhetor.config.settings import LimitsConfig
from deeprhetor.domain.enums import ClaimStatus, VerificationDecisionKind
from deeprhetor.domain.knowledge import (
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, ClaimTransitionError, EvidenceRepository
from deeprhetor.repositories.scan import ScanRepository
from deeprhetor.services.critic import CoverageCriticService, CriticLoopState
from deeprhetor.services.project_store import create_project_async
from deeprhetor.services.scan import ScanService
from deeprhetor.services.verify import VerifierService
from deeprhetor.workflow import (
    FakeCoverageCritic,
    FakeSupervisor,
    FakeTopicWorker,
    open_workflow,
    resume_with_approval,
    start_until_plan_interrupt,
)


@pytest.mark.asyncio
async def test_scan_incomplete_until_all_segments_terminal(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "scan.deeprhetor", title="S", prompt="p")
    docs = DocumentRepository(opened.engine)
    segments = [
        "Segment alpha about rhetoric.",
        "Segment beta about evidence.",
        "Segment gamma about claims.",
    ]
    doc, version, _segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Rhetoric notes",
        segments=segments,
    )
    scanner = ScanService(opened.engine, batch_size=2)
    batch1 = await scanner.scan_batch(
        document_id=doc.id, document_version_id=version.id, batch_index=0
    )
    assert batch1.document_scan.is_complete is False
    assert batch1.remaining == 1
    assert len(batch1.scanned_segment_ids) == 2

    scans = ScanRepository(opened.engine)
    mid = await scans.get_document_scan(version.id)
    assert mid is not None
    assert mid.is_complete is False

    final = await scanner.scan_until_complete(
        document_id=doc.id, document_version_id=version.id
    )
    assert final.is_complete is True
    assert final.total_segments == 3
    assert final.completed_segments == 3
    remaining = await scans.list_unscanned_segment_ids(version.id)
    assert remaining == []
    await opened.dispose()


@pytest.mark.asyncio
async def test_bad_quote_fails_verification(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "verify.deeprhetor", title="V", prompt="p")
    docs = DocumentRepository(opened.engine)
    doc, version, segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Source",
        segments=["The archived sentence is exactly this."],
    )
    claims = ClaimRepository(opened.engine)
    evidence_repo = EvidenceRepository(opened.engine)
    claim = await claims.create(
        ProposedClaim(statement="Something unsupported"),
        project_id=opened.project.id,
    )
    bad = Evidence(
        document_id=doc.id,
        document_version_id=version.id,
        document_segment_id=segs[0].id,
        quote="this quote does not appear in the archive",
        location=EvidenceLocation(char_start=0, char_end=10),
        content_hash=quote_content_hash("this quote does not appear in the archive"),
    )
    stored_ev = await evidence_repo.create(bad)
    await claims.attach_evidence(claim.id, stored_ev.id)

    verifier = VerifierService(opened.engine, claims=claims, evidence=evidence_repo, documents=docs)
    decision = await verifier.verify_claim(claim.id)
    assert decision.decision == VerificationDecisionKind.REJECT
    assert decision.quote_check_passed is False
    refreshed = await claims.get(claim.id)
    assert refreshed is not None
    assert refreshed.status == ClaimStatus.REJECTED

    # Good quote → approve
    claim2 = await claims.create(
        ProposedClaim(statement="Matches archive"),
        project_id=opened.project.id,
    )
    quote = "exactly this"
    good = Evidence(
        document_id=doc.id,
        document_version_id=version.id,
        document_segment_id=segs[0].id,
        quote=quote,
        location=EvidenceLocation(),  # substring match against segment
        content_hash=quote_content_hash(quote),
    )
    stored_good = await evidence_repo.create(good)
    await claims.attach_evidence(claim2.id, stored_good.id)
    decision2 = await verifier.verify_claim(claim2.id)
    assert decision2.decision == VerificationDecisionKind.APPROVE
    assert (await claims.get(claim2.id)).status == ClaimStatus.APPROVED  # type: ignore[union-attr]
    await opened.dispose()


@pytest.mark.asyncio
async def test_claim_state_transitions(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "claim.deeprhetor", title="C", prompt="p")
    claims = ClaimRepository(opened.engine)
    stored = await claims.create(
        ProposedClaim(statement="Atomic proposition"),
        project_id=opened.project.id,
    )
    assert stored.status == ClaimStatus.PROPOSED

    approved = await claims.transition(stored.id, ClaimStatus.APPROVED)
    assert approved.status == ClaimStatus.APPROVED

    with pytest.raises(ClaimTransitionError):
        await claims.transition(stored.id, ClaimStatus.PROPOSED)

    superseded = await claims.transition(stored.id, ClaimStatus.SUPERSEDED)
    assert superseded.status == ClaimStatus.SUPERSEDED

    with pytest.raises(ClaimTransitionError):
        await claims.transition(stored.id, ClaimStatus.APPROVED)

    other = await claims.create(
        ProposedClaim(statement="Needs work"),
        project_id=opened.project.id,
    )
    await claims.transition(other.id, ClaimStatus.NEEDS_CORRECTION)
    reproposed = await claims.transition(other.id, ClaimStatus.PROPOSED)
    assert reproposed.status == ClaimStatus.PROPOSED
    rejected = await claims.transition(other.id, ClaimStatus.REJECTED)
    assert rejected.status == ClaimStatus.REJECTED
    with pytest.raises(ClaimTransitionError):
        await claims.transition(other.id, ClaimStatus.APPROVED)
    await opened.dispose()


@pytest.mark.asyncio
async def test_critic_terminates_on_max_passes(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "critic.deeprhetor", title="K", prompt="p")
    limits = LimitsConfig(max_critic_passes=2, max_run_duration_seconds=3600)
    critic = CoverageCriticService(opened.engine, limits=limits)
    plan = ResearchPlan(
        project_id=opened.project.id,
        prompt="p",
        topics=[
            PlanTopic(
                topic_id="t1",
                title="Uncovered",
                objective="needs claims",
            )
        ],
        sections=[
            PlanSection(
                section_id="s1",
                title="Main",
                topic_ids=["t1"],
            )
        ],
    )
    state = CriticLoopState()
    r1 = await critic.evaluate(plan=plan, project_id=opened.project.id, state=state)
    assert r1.should_continue is True
    assert r1.state.pass_count == 1
    assert r1.report.is_complete is False
    assert r1.report.gaps

    r2 = await critic.evaluate(plan=plan, project_id=opened.project.id, state=r1.state)
    assert r2.should_continue is False
    assert r2.state.pass_count == 2
    assert r2.state.terminated_reason == "max_critic_passes"
    await opened.dispose()


@pytest.mark.asyncio
async def test_critic_terminates_on_completion(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "complete.deeprhetor", title="K2", prompt="p")
    docs = DocumentRepository(opened.engine)
    doc, version, segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Corpus",
        segments=["Complete coverage quote for topic."],
    )
    # Full scan so critic does not emit unscanned gap.
    await ScanService(opened.engine, batch_size=10).scan_until_complete(
        document_id=doc.id, document_version_id=version.id
    )

    claims = ClaimRepository(opened.engine)
    evidence_repo = EvidenceRepository(opened.engine)
    quote = "Complete coverage quote"
    for topic_id, statement in [
        ("t1", "Claim for topic one"),
        ("t2", "Claim for topic two"),
    ]:
        stored = await claims.create(
            ProposedClaim(statement=statement, topic_id=topic_id),
            project_id=opened.project.id,
        )
        ev = await evidence_repo.create(
            Evidence(
                document_id=doc.id,
                document_version_id=version.id,
                document_segment_id=segs[0].id,
                quote=quote,
                content_hash=quote_content_hash(quote),
            )
        )
        await claims.attach_evidence(stored.id, ev.id)
        await claims.transition(stored.id, ClaimStatus.APPROVED)

    plan = ResearchPlan(
        project_id=opened.project.id,
        prompt="p",
        topics=[
            PlanTopic(topic_id="t1", title="One", objective="o"),
            PlanTopic(topic_id="t2", title="Two", objective="o"),
        ],
        sections=[
            PlanSection(section_id="s1", title="All", topic_ids=["t1", "t2"]),
        ],
    )
    critic = CoverageCriticService(opened.engine, limits=LimitsConfig(max_critic_passes=5))
    result = await critic.evaluate(plan=plan, project_id=opened.project.id)
    assert result.report.is_complete is True
    assert result.should_continue is False
    assert result.state.terminated_reason == "complete"
    await opened.dispose()


@pytest.mark.asyncio
async def test_workflow_knowledge_loop_completes(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "wf7.deeprhetor", title="WF7", prompt="Explain")
    handle = await open_workflow(
        opened.engine,
        project_id=opened.project.id,
        configuration_snapshot_id=(
            opened.configuration_snapshot.id if opened.configuration_snapshot else None
        ),
        supervisor=FakeSupervisor(),
        worker=FakeTopicWorker(),
        critic=FakeCoverageCritic(force_complete=True),
    )
    await start_until_plan_interrupt(handle)
    chunks = await resume_with_approval(handle, action="approve")
    assert chunks
    run = await handle.ctx.runs.get(handle.run.id)
    assert run is not None
    assert run.status.value == "completed"
    events = await handle.ctx.events.list_for_run(handle.run.id)
    kinds = {e.kind for e in events}
    assert "workflow.scan" in kinds
    assert "workflow.propose" in kinds
    assert "workflow.verify" in kinds
    assert "workflow.critic" in kinds
    assert "workflow.completed" in kinds
    await opened.dispose()
