"""Project repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import BaseRepository, dumps_json, iso_now, loads_json, parse_dt, utcnow


class Project(BaseModel):
    """Typed project row returned by repositories."""

    id: str
    title: str
    prompt: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfigurationSnapshot(BaseModel):
    id: str
    project_id: str
    created_at: datetime
    label: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    model_presets: dict[str, Any] = Field(default_factory=dict)
    credential_refs: dict[str, Any] = Field(default_factory=dict)


class ProjectRepository(BaseRepository):
    """CRUD for the project and configuration_snapshot tables."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(self, *, title: str, prompt: str, project_id: str | None = None) -> Project:
        pid = project_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO project (id, title, prompt, created_at, updated_at, status) "
                    "VALUES (:id, :title, :prompt, :created_at, :updated_at, 'active')"
                ),
                {
                    "id": pid,
                    "title": title,
                    "prompt": prompt,
                    "created_at": iso,
                    "updated_at": iso,
                },
            )
        return Project(
            id=pid,
            title=title,
            prompt=prompt,
            created_at=now,
            updated_at=now,
        )

    async def get(self, project_id: str) -> Project | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, title, prompt, status, created_at, updated_at, metadata_json "
                    "FROM project WHERE id = :id"
                ),
                {"id": project_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return Project(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            status=row["status"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
            metadata=loads_json(row["metadata_json"]),
        )

    async def get_sole(self) -> Project | None:
        """Return the single project row in a one-project database, if any."""
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, title, prompt, status, created_at, updated_at, metadata_json "
                    "FROM project ORDER BY created_at ASC LIMIT 1"
                )
            )
            row = result.mappings().first()
        if row is None:
            return None
        return Project(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            status=row["status"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
            metadata=loads_json(row["metadata_json"]),
        )

    async def list_projects(self) -> list[Project]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, title, prompt, status, created_at, updated_at, metadata_json "
                    "FROM project ORDER BY created_at DESC"
                )
            )
            rows = result.mappings().all()
        return [
            Project(
                id=row["id"],
                title=row["title"],
                prompt=row["prompt"],
                status=row["status"],
                created_at=parse_dt(row["created_at"]) or utcnow(),
                updated_at=parse_dt(row["updated_at"]) or utcnow(),
                metadata=loads_json(row.get("metadata_json")),
            )
            for row in rows
        ]

    async def create_configuration_snapshot(
        self,
        *,
        project_id: str,
        settings: dict[str, Any],
        model_presets: dict[str, Any] | None = None,
        credential_refs: dict[str, Any] | None = None,
        label: str | None = None,
        snapshot_id: str | None = None,
    ) -> ConfigurationSnapshot:
        sid = snapshot_id or str(uuid4())
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO configuration_snapshot "
                    "(id, project_id, created_at, label, settings_json, "
                    "model_presets_json, credential_refs_json) "
                    "VALUES (:id, :project_id, :created_at, :label, :settings_json, "
                    ":model_presets_json, :credential_refs_json)"
                ),
                {
                    "id": sid,
                    "project_id": project_id,
                    "created_at": iso_now(),
                    "label": label,
                    "settings_json": dumps_json(settings),
                    "model_presets_json": dumps_json(model_presets or {}),
                    "credential_refs_json": dumps_json(credential_refs or {}),
                },
            )
        return ConfigurationSnapshot(
            id=sid,
            project_id=project_id,
            created_at=now,
            label=label,
            settings=settings,
            model_presets=model_presets or {},
            credential_refs=credential_refs or {},
        )

    async def get_configuration_snapshot(
        self, snapshot_id: str
    ) -> ConfigurationSnapshot | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, created_at, label, settings_json, "
                    "model_presets_json, credential_refs_json "
                    "FROM configuration_snapshot WHERE id = :id"
                ),
                {"id": snapshot_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return ConfigurationSnapshot(
            id=row["id"],
            project_id=row["project_id"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            label=row["label"],
            settings=loads_json(row["settings_json"]),
            model_presets=loads_json(row["model_presets_json"]),
            credential_refs=loads_json(row["credential_refs_json"]),
        )
