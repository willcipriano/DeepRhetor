"""Migration and repository smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from deeprhetor.db import SCHEMA_VERSION, apply_migrations, create_sync_engine_for_path
from deeprhetor.repositories import ProjectRepository


EXPECTED_TABLES = {
    "schema_version",
    "project",
    "configuration_snapshot",
    "run",
    "task",
    "task_dependency",
    "checkpoint",
    "event",
    "error",
    "research_plan",
    "plan_topic",
    "plan_section",
    "plan_amendment",
    "search_query",
    "search_hit",
    "provider_call",
    "document",
    "document_version",
    "document_blob",
    "document_segment",
    "document_fts",
    "relevance_assessment",
    "segment_scan",
    "document_scan",
    "claim",
    "evidence",
    "claim_evidence",
    "claim_relation",
    "claim_fts",
    "outline",
    "draft",
    "draft_section",
    "citation_key",
    "model_call",
    "usage_record",
    "artifact",
    "validation_result",
}


def test_migrations_apply_to_temp_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "project.db"
    apply_migrations(db_path)

    engine = create_sync_engine_for_path(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        ).fetchall()
        names = {r[0] for r in rows}
        version = conn.execute(
            text("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        ).scalar_one()
    engine.dispose()

    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {sorted(missing)}"
    assert version == SCHEMA_VERSION
    assert db_path.is_file()


@pytest.mark.asyncio
async def test_project_repository_create_and_get(tmp_path: Path) -> None:
    db_path = tmp_path / "repo.db"
    apply_migrations(db_path)

    repo = ProjectRepository.from_path(db_path)
    created = await repo.create(title="Demo", prompt="Explain topic X")
    fetched = await repo.get(created.id)
    await repo.dispose()

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "Demo"
    assert fetched.prompt == "Explain topic X"
