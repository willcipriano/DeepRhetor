"""Portable project SQLite lifecycle: create, open, backup.

Preferred project file extensions are ``.deeprhetor`` or ``.sqlite``.
Each project is one file containing schema, corpus, workflow state, and artifacts.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.db import apply_migrations, create_async_engine_for_path, create_sync_engine_for_path
from deeprhetor.repositories.project import ConfigurationSnapshot, Project, ProjectRepository
from deeprhetor.services.recovery import RecoveryReport, mark_orphaned_in_progress

PREFERRED_EXTENSIONS = (".deeprhetor", ".sqlite")
DEFAULT_EXTENSION = ".deeprhetor"


@dataclass
class OpenProject:
    """An opened project database with typed accessors."""

    path: Path
    engine: AsyncEngine
    project: Project
    configuration_snapshot: ConfigurationSnapshot | None = None
    recovery: RecoveryReport | None = None

    async def dispose(self) -> None:
        await self.engine.dispose()


def normalize_project_path(path: Path | str) -> Path:
    """Ensure a project path uses a preferred extension when none is given."""
    p = Path(path)
    if p.suffix.lower() not in PREFERRED_EXTENSIONS and p.suffix == "":
        return p.with_suffix(DEFAULT_EXTENSION)
    return p


def create_project(
    path: Path | str,
    *,
    title: str,
    prompt: str,
    config_snapshot: dict[str, Any] | None = None,
    model_presets: dict[str, Any] | None = None,
    credential_refs: dict[str, Any] | None = None,
    label: str | None = "initial",
) -> OpenProject:
    """Create a new project SQLite file, apply migrations, and insert seed rows.

    Prefer ``.deeprhetor`` (or ``.sqlite``) as the file extension. The file is
    created empty, migrations bring schema to head, then a ``project`` row and
    ``configuration_snapshot`` are written.
    """
    import asyncio

    return asyncio.run(
        create_project_async(
            path,
            title=title,
            prompt=prompt,
            config_snapshot=config_snapshot,
            model_presets=model_presets,
            credential_refs=credential_refs,
            label=label,
        )
    )


async def create_project_async(
    path: Path | str,
    *,
    title: str,
    prompt: str,
    config_snapshot: dict[str, Any] | None = None,
    model_presets: dict[str, Any] | None = None,
    credential_refs: dict[str, Any] | None = None,
    label: str | None = "initial",
) -> OpenProject:
    db_path = normalize_project_path(path)
    if db_path.exists():
        raise FileExistsError(f"project already exists: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    apply_migrations(db_path)
    engine = create_async_engine_for_path(db_path)
    repo = ProjectRepository(engine)
    project = await repo.create(title=title, prompt=prompt)
    snapshot = await repo.create_configuration_snapshot(
        project_id=project.id,
        settings=config_snapshot or {},
        model_presets=model_presets,
        credential_refs=credential_refs,
        label=label,
    )
    return OpenProject(
        path=db_path,
        engine=engine,
        project=project,
        configuration_snapshot=snapshot,
    )


def open_project(path: Path | str, *, recover: bool = True) -> OpenProject:
    """Open an existing project file and enable WAL mode."""
    import asyncio

    return asyncio.run(open_project_async(path, recover=recover))


async def open_project_async(path: Path | str, *, recover: bool = True) -> OpenProject:
    db_path = Path(path)
    if not db_path.is_file():
        raise FileNotFoundError(f"project not found: {db_path}")

    # Ensure schema is current (idempotent upgrade to head).
    apply_migrations(db_path)

    engine = create_async_engine_for_path(db_path)
    # Confirm WAL is active (pragma registered on connect; verify explicitly).
    async with engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        if str(mode).lower() != "wal":
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.commit()

    repo = ProjectRepository(engine)
    project = await repo.get_sole()
    if project is None:
        await engine.dispose()
        raise RuntimeError(f"no project row in database: {db_path}")

    recovery: RecoveryReport | None = None
    if recover:
        recovery = await mark_orphaned_in_progress(engine)

    return OpenProject(
        path=db_path,
        engine=engine,
        project=project,
        recovery=recovery,
    )


def backup_project(path: Path | str, dest: Path | str) -> Path:
    """Checkpoint WAL then copy the project file safely to ``dest``.

    Uses ``PRAGMA wal_checkpoint(TRUNCATE)`` so all committed pages are in the
    main database file before the filesystem copy.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"project not found: {src}")
    dest_path = normalize_project_path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_sync_engine_for_path(src)
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.commit()
    finally:
        engine.dispose()

    shutil.copy2(src, dest_path)
    # Best-effort cleanup of leftover sidecars after truncate (may already be gone).
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(src) + suffix)
        # Do not delete source sidecars; TRUNCATE should have emptied the WAL.
        _ = sidecar
    return dest_path
