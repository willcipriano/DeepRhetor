"""Project repository stub."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import BaseRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(BaseModel):
    """Typed project row returned by repositories."""

    id: str
    title: str
    prompt: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRepository(BaseRepository):
    """CRUD stubs for the project table."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(self, *, title: str, prompt: str, project_id: str | None = None) -> Project:
        pid = project_id or str(uuid4())
        now = _utcnow()
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
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def list_projects(self) -> list[Project]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, title, prompt, status, created_at, updated_at "
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
                created_at=_parse_dt(row["created_at"]),
                updated_at=_parse_dt(row["updated_at"]),
            )
            for row in rows
        ]


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    # SQLite datetime('now') is naive UTC-ish strings.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
