"""Run and task ledger repositories."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import RunStatus, TaskStatus

from .base import BaseRepository, dumps_json, iso_now, loads_json, parse_dt, utcnow


class Run(BaseModel):
    id: str
    project_id: str
    configuration_snapshot_id: str | None = None
    status: RunStatus = RunStatus.CREATED
    plan_version: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    run_id: str
    parent_task_id: str | None = None
    kind: str
    status: TaskStatus = TaskStatus.PENDING
    assignment: dict[str, Any] | None = None
    idempotency_key: str | None = None
    attempt: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


class RunRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        *,
        project_id: str,
        configuration_snapshot_id: str | None = None,
        status: RunStatus = RunStatus.CREATED,
        plan_version: int | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        rid = run_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO run (id, project_id, configuration_snapshot_id, status, "
                    "plan_version, created_at, metadata_json) "
                    "VALUES (:id, :project_id, :configuration_snapshot_id, :status, "
                    ":plan_version, :created_at, :metadata_json)"
                ),
                {
                    "id": rid,
                    "project_id": project_id,
                    "configuration_snapshot_id": configuration_snapshot_id,
                    "status": str(status),
                    "plan_version": plan_version,
                    "created_at": iso,
                    "metadata_json": dumps_json(metadata or {}),
                },
            )
        return Run(
            id=rid,
            project_id=project_id,
            configuration_snapshot_id=configuration_snapshot_id,
            status=status,
            plan_version=plan_version,
            created_at=now,
            metadata=metadata or {},
        )

    async def get(self, run_id: str) -> Run | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, configuration_snapshot_id, status, plan_version, "
                    "started_at, finished_at, created_at, metadata_json "
                    "FROM run WHERE id = :id"
                ),
                {"id": run_id},
            )
            row = result.mappings().first()
        return _run_from_row(row) if row else None

    async def list_for_project(self, project_id: str) -> list[Run]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, configuration_snapshot_id, status, plan_version, "
                    "started_at, finished_at, created_at, metadata_json "
                    "FROM run WHERE project_id = :project_id ORDER BY created_at DESC"
                ),
                {"project_id": project_id},
            )
            rows = result.mappings().all()
        return [_run_from_row(row) for row in rows]

    async def update_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> Run | None:
        fields = ["status = :status"]
        params: dict[str, Any] = {"id": run_id, "status": str(status)}
        if started_at is not None:
            fields.append("started_at = :started_at")
            params["started_at"] = started_at.replace(microsecond=0).isoformat()
        if finished_at is not None:
            fields.append("finished_at = :finished_at")
            params["finished_at"] = finished_at.replace(microsecond=0).isoformat()
        elif status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABANDONED}:
            fields.append("finished_at = :finished_at")
            params["finished_at"] = iso_now()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE run SET {', '.join(fields)} WHERE id = :id"),
                params,
            )
        return await self.get(run_id)


class TaskRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        *,
        run_id: str,
        kind: str,
        status: TaskStatus = TaskStatus.PENDING,
        parent_task_id: str | None = None,
        assignment: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        task_id: str | None = None,
    ) -> Task:
        """Create a task, or reuse an existing row on (run_id, idempotency_key) replay."""
        if idempotency_key is not None:
            existing = await self.get_by_idempotency_key(run_id, idempotency_key)
            if existing is not None:
                return existing

        tid = task_id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO task (id, run_id, parent_task_id, kind, status, "
                        "assignment_json, idempotency_key, attempt, created_at, updated_at) "
                        "VALUES (:id, :run_id, :parent_task_id, :kind, :status, "
                        ":assignment_json, :idempotency_key, 0, :created_at, :updated_at)"
                    ),
                    {
                        "id": tid,
                        "run_id": run_id,
                        "parent_task_id": parent_task_id,
                        "kind": kind,
                        "status": str(status),
                        "assignment_json": dumps_json(assignment) if assignment is not None else None,
                        "idempotency_key": idempotency_key,
                        "created_at": iso,
                        "updated_at": iso,
                    },
                )
        except IntegrityError:
            # Concurrent replay races the UNIQUE(run_id, idempotency_key) constraint.
            if idempotency_key is not None:
                existing = await self.get_by_idempotency_key(run_id, idempotency_key)
                if existing is not None:
                    return existing
            raise

        return Task(
            id=tid,
            run_id=run_id,
            parent_task_id=parent_task_id,
            kind=kind,
            status=status,
            assignment=assignment,
            idempotency_key=idempotency_key,
            attempt=0,
            created_at=now,
            updated_at=now,
        )

    async def get(self, task_id: str) -> Task | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, parent_task_id, kind, status, assignment_json, "
                    "idempotency_key, attempt, created_at, updated_at, started_at, "
                    "finished_at, error_message FROM task WHERE id = :id"
                ),
                {"id": task_id},
            )
            row = result.mappings().first()
        return _task_from_row(row) if row else None

    async def get_by_idempotency_key(self, run_id: str, idempotency_key: str) -> Task | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, parent_task_id, kind, status, assignment_json, "
                    "idempotency_key, attempt, created_at, updated_at, started_at, "
                    "finished_at, error_message FROM task "
                    "WHERE run_id = :run_id AND idempotency_key = :idempotency_key"
                ),
                {"run_id": run_id, "idempotency_key": idempotency_key},
            )
            row = result.mappings().first()
        return _task_from_row(row) if row else None

    async def list_for_run(self, run_id: str) -> list[Task]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, parent_task_id, kind, status, assignment_json, "
                    "idempotency_key, attempt, created_at, updated_at, started_at, "
                    "finished_at, error_message FROM task "
                    "WHERE run_id = :run_id ORDER BY created_at ASC"
                ),
                {"run_id": run_id},
            )
            rows = result.mappings().all()
        return [_task_from_row(row) for row in rows]

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_message: str | None = None,
        increment_attempt: bool = False,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> Task | None:
        fields = ["status = :status", "updated_at = :updated_at"]
        params: dict[str, Any] = {
            "id": task_id,
            "status": str(status),
            "updated_at": iso_now(),
        }
        if error_message is not None:
            fields.append("error_message = :error_message")
            params["error_message"] = error_message
        if increment_attempt:
            fields.append("attempt = attempt + 1")
        if started_at is not None:
            fields.append("started_at = :started_at")
            params["started_at"] = started_at.replace(microsecond=0).isoformat()
        if finished_at is not None:
            fields.append("finished_at = :finished_at")
            params["finished_at"] = finished_at.replace(microsecond=0).isoformat()
        elif status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }:
            fields.append("finished_at = COALESCE(finished_at, :finished_at)")
            params["finished_at"] = iso_now()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE task SET {', '.join(fields)} WHERE id = :id"),
                params,
            )
        return await self.get(task_id)

    async def add_dependency(self, task_id: str, depends_on_task_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO task_dependency (task_id, depends_on_task_id) "
                    "VALUES (:task_id, :depends_on_task_id)"
                ),
                {"task_id": task_id, "depends_on_task_id": depends_on_task_id},
            )

    async def list_dependencies(self, task_id: str) -> list[str]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT depends_on_task_id FROM task_dependency WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            return [row[0] for row in result.fetchall()]


def _run_from_row(row: Any) -> Run:
    return Run(
        id=row["id"],
        project_id=row["project_id"],
        configuration_snapshot_id=row["configuration_snapshot_id"],
        status=RunStatus(row["status"]),
        plan_version=row["plan_version"],
        started_at=parse_dt(row["started_at"]),
        finished_at=parse_dt(row["finished_at"]),
        created_at=parse_dt(row["created_at"]) or utcnow(),
        metadata=loads_json(row["metadata_json"]),
    )


def _task_from_row(row: Any) -> Task:
    assignment_raw = row["assignment_json"]
    assignment = loads_json(assignment_raw) if assignment_raw else None
    return Task(
        id=row["id"],
        run_id=row["run_id"],
        parent_task_id=row["parent_task_id"],
        kind=row["kind"],
        status=TaskStatus(row["status"]),
        assignment=assignment,
        idempotency_key=row["idempotency_key"],
        attempt=row["attempt"],
        created_at=parse_dt(row["created_at"]) or utcnow(),
        updated_at=parse_dt(row["updated_at"]) or utcnow(),
        started_at=parse_dt(row["started_at"]),
        finished_at=parse_dt(row["finished_at"]),
        error_message=row["error_message"],
    )
