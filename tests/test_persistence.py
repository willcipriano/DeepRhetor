"""Stage 2 persistence and recovery tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from deeprhetor.domain.enums import RunStatus, TaskStatus
from deeprhetor.repositories import (
    ArtifactRepository,
    DocumentRepository,
    RunRepository,
    TaskRepository,
)
from deeprhetor.services.checkpoint import CheckpointStore
from deeprhetor.services.fts import FtsService
from deeprhetor.services.project_store import (
    backup_project,
    create_project_async,
    open_project_async,
)
from deeprhetor.services.recovery import RecoveryService


@pytest.mark.asyncio
async def test_create_reopen_backup_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "demo.deeprhetor"
    opened = await create_project_async(
        src,
        title="Demo",
        prompt="Explain Stage 2",
        config_snapshot={"limits": {"max_critic_passes": 3}},
    )
    assert opened.path == src
    assert opened.path.is_file()
    assert opened.project.title == "Demo"
    assert opened.configuration_snapshot is not None
    assert opened.configuration_snapshot.settings["limits"]["max_critic_passes"] == 3
    project_id = opened.project.id
    await opened.dispose()

    reopened = await open_project_async(src)
    assert reopened.project.id == project_id
    async with reopened.engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
    assert str(mode).lower() == "wal"
    await reopened.dispose()

    dest = tmp_path / "backup.deeprhetor"
    backup_project(src, dest)
    assert dest.is_file()

    backed = await open_project_async(dest)
    assert backed.project.id == project_id
    assert backed.project.prompt == "Explain Stage 2"
    await backed.dispose()


@pytest.mark.asyncio
async def test_interrupted_run_recovery_on_open(tmp_path: Path) -> None:
    path = tmp_path / "recover.deeprhetor"
    opened = await create_project_async(path, title="R", prompt="p")
    runs = RunRepository(opened.engine)
    tasks = TaskRepository(opened.engine)
    run = await runs.create(project_id=opened.project.id, status=RunStatus.RUNNING)
    task = await tasks.create(
        run_id=run.id,
        kind="scan",
        status=TaskStatus.RUNNING,
        idempotency_key="scan-1",
    )
    await opened.dispose()

    reopened = await open_project_async(path)
    assert reopened.recovery is not None
    assert run.id in reopened.recovery.interrupted_run_ids
    assert task.id in reopened.recovery.interrupted_task_ids

    runs2 = RunRepository(reopened.engine)
    tasks2 = TaskRepository(reopened.engine)
    recovered_run = await runs2.get(run.id)
    recovered_task = await tasks2.get(task.id)
    assert recovered_run is not None
    assert recovered_task is not None
    assert recovered_run.status == RunStatus.INTERRUPTED
    assert recovered_task.status == TaskStatus.INTERRUPTED

    recovery = RecoveryService(reopened.engine)
    resumed = await recovery.resume_run(run.id)
    assert resumed.status == RunStatus.RUNNING
    retried = await recovery.retry_task(task.id)
    assert retried.status == TaskStatus.PENDING
    assert retried.attempt == 1

    abandoned = await recovery.abandon_run(run.id)
    assert abandoned.status == RunStatus.ABANDONED
    cancelled = await tasks2.get(task.id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    await reopened.dispose()


@pytest.mark.asyncio
async def test_idempotent_task_and_artifact_inserts(tmp_path: Path) -> None:
    path = tmp_path / "idem.deeprhetor"
    opened = await create_project_async(path, title="I", prompt="p")
    runs = RunRepository(opened.engine)
    tasks = TaskRepository(opened.engine)
    artifacts = ArtifactRepository(opened.engine)

    run = await runs.create(project_id=opened.project.id)
    first = await tasks.create(
        run_id=run.id,
        kind="worker",
        idempotency_key="topic:alpha",
        assignment={"topic": "alpha"},
    )
    second = await tasks.create(
        run_id=run.id,
        kind="worker",
        idempotency_key="topic:alpha",
        assignment={"topic": "alpha-dup"},
    )
    assert first.id == second.id
    listed = await tasks.list_for_run(run.id)
    assert len(listed) == 1

    art1 = await artifacts.create(
        project_id=opened.project.id,
        kind="manifest",
        path_or_name="manifest.json",
        data=b'{"ok": true}',
        idempotency_key="artifact:manifest:v1",
    )
    art2 = await artifacts.create(
        project_id=opened.project.id,
        kind="manifest",
        path_or_name="manifest.json",
        data=b'{"ok": false}',
        idempotency_key="artifact:manifest:v1",
    )
    assert art1.id == art2.id
    await opened.dispose()


@pytest.mark.asyncio
async def test_fts_index_and_search(tmp_path: Path) -> None:
    path = tmp_path / "fts.deeprhetor"
    opened = await create_project_async(path, title="FTS", prompt="p")
    docs = DocumentRepository(opened.engine)
    _doc, version, segments = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Boiling water notes",
        segments=[
            "Water boils at one hundred degrees Celsius at standard pressure.",
            "At high altitude the boiling point is lower.",
        ],
    )
    assert len(segments) == 2

    fts = FtsService(opened.engine)
    hits = await fts.search_documents("boiling")
    assert hits
    assert any("boiling" in h.text.lower() for h in hits)
    assert any(h.document_version_id == version.id for h in hits)

    async with opened.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO claim (id, project_id, statement, status) "
                "VALUES ('c1', :pid, 'Water boils at 100C at 1 atm', 'proposed')"
            ),
            {"pid": opened.project.id},
        )
    await fts.index_claim("c1", "Water boils at 100C at 1 atm")
    claim_hits = await fts.search_claims("boils")
    assert claim_hits
    assert claim_hits[0].claim_id == "c1"
    await opened.dispose()


@pytest.mark.asyncio
async def test_checkpoint_namespace_helper(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.deeprhetor"
    opened = await create_project_async(path, title="C", prompt="p")
    runs = RunRepository(opened.engine)
    run = await runs.create(project_id=opened.project.id)
    store = CheckpointStore(opened.engine)
    stored = await store.put(
        run_id=run.id,
        node_name="plan",
        namespace="graph",
        payload={"cursor": 1},
    )
    assert stored.checkpoint_ns.startswith("lg:")
    latest = await store.latest(run_id=run.id, node_name="plan", namespace="graph")
    assert latest is not None
    assert latest.payload["cursor"] == 1
    listed = await store.list_for_run(run.id)
    assert len(listed) == 1
    await opened.dispose()
