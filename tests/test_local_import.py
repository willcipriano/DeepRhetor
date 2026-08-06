"""Local file importer into document archive."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from deeprhetor.services.local_import import LocalFileImporter
from deeprhetor.services.project_store import create_project_async


@pytest.mark.asyncio
async def test_import_markdown_into_archive(repo_root: Path, tmp_path: Path) -> None:
    project_path = tmp_path / "local.deeprhetor"
    opened = await create_project_async(project_path, title="Local", prompt="import")
    try:
        importer = LocalFileImporter(opened.engine)
        src = repo_root / "tests" / "fixtures" / "documents" / "sample.md"
        document, version, segments = await importer.import_path(
            src, project_id=opened.project.id
        )
        assert document.source_class == "local"
        assert document.media_type == "text/markdown"
        assert version.parser == "markdown"
        assert segments
        assert all(s.status == "pending" for s in segments)
        assert segments[0].char_start is not None

        async with opened.engine.connect() as conn:
            blob = (
                await conn.execute(
                    text(
                        "SELECT byte_size, sha256 FROM document_blob "
                        "WHERE document_version_id = :vid"
                    ),
                    {"vid": version.id},
                )
            ).mappings().first()
        assert blob is not None
        assert blob["byte_size"] == src.stat().st_size
        assert len(blob["sha256"]) == 64
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_import_pdf_into_archive(repo_root: Path, tmp_path: Path) -> None:
    project_path = tmp_path / "pdf.deeprhetor"
    opened = await create_project_async(project_path, title="PDF", prompt="import")
    try:
        importer = LocalFileImporter(opened.engine)
        src = repo_root / "tests" / "fixtures" / "documents" / "sample.pdf"
        document, version, segments = await importer.import_path(
            src, project_id=opened.project.id, title="PDF Fixture"
        )
        assert document.media_type == "application/pdf"
        assert version.parser == "pymupdf"
        assert any(s.page == 1 for s in segments)
    finally:
        await opened.dispose()
