"""Event, error, artifact, model-call, and usage repositories."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import BaseRepository, dumps_json, iso_now, loads_json, parse_dt, utcnow


class Event(BaseModel):
    id: str
    run_id: str | None = None
    task_id: str | None = None
    level: str = "info"
    kind: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ErrorRecord(BaseModel):
    id: str
    run_id: str | None = None
    task_id: str | None = None
    code: str | None = None
    message: str
    traceback: str | None = None
    created_at: datetime


class Artifact(BaseModel):
    id: str
    project_id: str
    run_id: str | None = None
    kind: str
    media_type: str | None = None
    path_or_name: str | None = None
    sha256: str | None = None
    byte_size: int | None = None
    idempotency_key: str | None = None
    created_at: datetime
    has_data: bool = False


class ModelCall(BaseModel):
    id: str
    run_id: str | None = None
    task_id: str | None = None
    role: str | None = None
    provider: str
    model_id: str
    idempotency_key: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    status: str = "ok"
    latency_ms: int | None = None
    created_at: datetime


class UsageRecord(BaseModel):
    id: str
    model_call_id: str | None = None
    run_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_cost_usd: float | None = None
    created_at: datetime


class EventRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        *,
        kind: str,
        message: str,
        run_id: str | None = None,
        task_id: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> Event:
        eid = event_id or str(uuid4())
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO event (id, run_id, task_id, level, kind, message, "
                    "payload_json, created_at) "
                    "VALUES (:id, :run_id, :task_id, :level, :kind, :message, "
                    ":payload_json, :created_at)"
                ),
                {
                    "id": eid,
                    "run_id": run_id,
                    "task_id": task_id,
                    "level": level,
                    "kind": kind,
                    "message": message,
                    "payload_json": dumps_json(payload or {}),
                    "created_at": iso_now(),
                },
            )
        return Event(
            id=eid,
            run_id=run_id,
            task_id=task_id,
            level=level,
            kind=kind,
            message=message,
            payload=payload or {},
            created_at=now,
        )

    async def list_for_run(self, run_id: str) -> list[Event]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, task_id, level, kind, message, payload_json, created_at "
                    "FROM event WHERE run_id = :run_id ORDER BY created_at ASC, id ASC"
                ),
                {"run_id": run_id},
            )
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    async def list_for_run_after(
        self,
        run_id: str,
        *,
        after_id: str | None = None,
        limit: int = 200,
    ) -> list[Event]:
        """Return events for a run strictly after ``after_id`` (cursor by created_at, id)."""
        if after_id is None:
            async with self.connection() as conn:
                result = await conn.execute(
                    text(
                        "SELECT id, run_id, task_id, level, kind, message, payload_json, created_at "
                        "FROM event WHERE run_id = :run_id "
                        "ORDER BY created_at ASC, id ASC LIMIT :limit"
                    ),
                    {"run_id": run_id, "limit": limit},
                )
                rows = result.mappings().all()
            return [self._from_row(row) for row in rows]

        async with self.connection() as conn:
            cursor = await conn.execute(
                text("SELECT created_at, id FROM event WHERE id = :id"),
                {"id": after_id},
            )
            cursor_row = cursor.mappings().first()
            if cursor_row is None:
                result = await conn.execute(
                    text(
                        "SELECT id, run_id, task_id, level, kind, message, payload_json, created_at "
                        "FROM event WHERE run_id = :run_id "
                        "ORDER BY created_at ASC, id ASC LIMIT :limit"
                    ),
                    {"run_id": run_id, "limit": limit},
                )
            else:
                result = await conn.execute(
                    text(
                        "SELECT id, run_id, task_id, level, kind, message, payload_json, created_at "
                        "FROM event WHERE run_id = :run_id AND "
                        "(created_at > :created_at OR "
                        "(created_at = :created_at AND id > :after_id)) "
                        "ORDER BY created_at ASC, id ASC LIMIT :limit"
                    ),
                    {
                        "run_id": run_id,
                        "created_at": cursor_row["created_at"],
                        "after_id": after_id,
                        "limit": limit,
                    },
                )
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: Any) -> Event:
        return Event(
            id=row["id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            level=row["level"],
            kind=row["kind"],
            message=row["message"],
            payload=loads_json(row["payload_json"]),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )


class ErrorRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        *,
        message: str,
        run_id: str | None = None,
        task_id: str | None = None,
        code: str | None = None,
        traceback: str | None = None,
        error_id: str | None = None,
    ) -> ErrorRecord:
        eid = error_id or str(uuid4())
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO error (id, run_id, task_id, code, message, traceback, created_at) "
                    "VALUES (:id, :run_id, :task_id, :code, :message, :traceback, :created_at)"
                ),
                {
                    "id": eid,
                    "run_id": run_id,
                    "task_id": task_id,
                    "code": code,
                    "message": message,
                    "traceback": traceback,
                    "created_at": iso_now(),
                },
            )
        return ErrorRecord(
            id=eid,
            run_id=run_id,
            task_id=task_id,
            code=code,
            message=message,
            traceback=traceback,
            created_at=now,
        )

    async def list_for_run(self, run_id: str) -> list[ErrorRecord]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, task_id, code, message, traceback, created_at "
                    "FROM error WHERE run_id = :run_id ORDER BY created_at ASC"
                ),
                {"run_id": run_id},
            )
            rows = result.mappings().all()
        return [
            ErrorRecord(
                id=row["id"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                code=row["code"],
                message=row["message"],
                traceback=row["traceback"],
                created_at=parse_dt(row["created_at"]) or utcnow(),
            )
            for row in rows
        ]


class ArtifactRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Artifact | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, kind, media_type, path_or_name, sha256, "
                    "byte_size, idempotency_key, created_at, "
                    "(data IS NOT NULL) AS has_data "
                    "FROM artifact WHERE idempotency_key = :idempotency_key"
                ),
                {"idempotency_key": idempotency_key},
            )
            row = result.mappings().first()
        return _artifact_from_row(row) if row else None

    async def create(
        self,
        *,
        project_id: str,
        kind: str,
        run_id: str | None = None,
        media_type: str | None = None,
        path_or_name: str | None = None,
        sha256: str | None = None,
        data: bytes | None = None,
        idempotency_key: str | None = None,
        artifact_id: str | None = None,
    ) -> Artifact:
        """Create an artifact, or reuse an existing row when idempotency_key matches."""
        if idempotency_key is not None:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        aid = artifact_id or str(uuid4())
        now = utcnow()
        byte_size = len(data) if data is not None else None
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO artifact (id, project_id, run_id, kind, media_type, "
                        "path_or_name, sha256, byte_size, data, created_at, idempotency_key) "
                        "VALUES (:id, :project_id, :run_id, :kind, :media_type, "
                        ":path_or_name, :sha256, :byte_size, :data, :created_at, "
                        ":idempotency_key)"
                    ),
                    {
                        "id": aid,
                        "project_id": project_id,
                        "run_id": run_id,
                        "kind": kind,
                        "media_type": media_type,
                        "path_or_name": path_or_name,
                        "sha256": sha256,
                        "byte_size": byte_size,
                        "data": data,
                        "created_at": iso_now(),
                        "idempotency_key": idempotency_key,
                    },
                )
        except IntegrityError:
            if idempotency_key is not None:
                existing = await self.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        return Artifact(
            id=aid,
            project_id=project_id,
            run_id=run_id,
            kind=kind,
            media_type=media_type,
            path_or_name=path_or_name,
            sha256=sha256,
            byte_size=byte_size,
            idempotency_key=idempotency_key,
            created_at=now,
            has_data=data is not None,
        )

    async def get(self, artifact_id: str) -> Artifact | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, kind, media_type, path_or_name, sha256, "
                    "byte_size, idempotency_key, created_at, "
                    "(data IS NOT NULL) AS has_data "
                    "FROM artifact WHERE id = :id"
                ),
                {"id": artifact_id},
            )
            row = result.mappings().first()
        return _artifact_from_row(row) if row else None

    async def get_data(self, artifact_id: str) -> bytes | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text("SELECT data FROM artifact WHERE id = :id"),
                {"id": artifact_id},
            )
            row = result.first()
        if row is None:
            return None
        data = row[0]
        return bytes(data) if data is not None else None

    async def list_for_project(self, project_id: str) -> list[Artifact]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, kind, media_type, path_or_name, sha256, "
                    "byte_size, idempotency_key, created_at, "
                    "(data IS NOT NULL) AS has_data "
                    "FROM artifact WHERE project_id = :project_id "
                    "ORDER BY created_at DESC"
                ),
                {"project_id": project_id},
            )
            rows = result.mappings().all()
        return [_artifact_from_row(row) for row in rows]

    async def list_for_run(self, run_id: str) -> list[Artifact]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, kind, media_type, path_or_name, sha256, "
                    "byte_size, idempotency_key, created_at, "
                    "(data IS NOT NULL) AS has_data "
                    "FROM artifact WHERE run_id = :run_id "
                    "ORDER BY created_at DESC"
                ),
                {"run_id": run_id},
            )
            rows = result.mappings().all()
        return [_artifact_from_row(row) for row in rows]


class ModelCallRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ModelCall | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, run_id, task_id, role, provider, model_id, idempotency_key, "
                    "request_json, response_json, status, latency_ms, created_at "
                    "FROM model_call WHERE idempotency_key = :idempotency_key"
                ),
                {"idempotency_key": idempotency_key},
            )
            row = result.mappings().first()
        return _model_call_from_row(row) if row else None

    async def create(
        self,
        *,
        provider: str,
        model_id: str,
        run_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
        idempotency_key: str | None = None,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        status: str = "ok",
        latency_ms: int | None = None,
        call_id: str | None = None,
    ) -> ModelCall:
        if idempotency_key is not None:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        cid = call_id or str(uuid4())
        now = utcnow()
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO model_call (id, run_id, task_id, role, provider, model_id, "
                        "idempotency_key, request_json, response_json, status, latency_ms, "
                        "created_at) "
                        "VALUES (:id, :run_id, :task_id, :role, :provider, :model_id, "
                        ":idempotency_key, :request_json, :response_json, :status, "
                        ":latency_ms, :created_at)"
                    ),
                    {
                        "id": cid,
                        "run_id": run_id,
                        "task_id": task_id,
                        "role": role,
                        "provider": provider,
                        "model_id": model_id,
                        "idempotency_key": idempotency_key,
                        "request_json": dumps_json(request) if request is not None else None,
                        "response_json": dumps_json(response) if response is not None else None,
                        "status": status,
                        "latency_ms": latency_ms,
                        "created_at": iso_now(),
                    },
                )
        except IntegrityError:
            if idempotency_key is not None:
                existing = await self.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        return ModelCall(
            id=cid,
            run_id=run_id,
            task_id=task_id,
            role=role,
            provider=provider,
            model_id=model_id,
            idempotency_key=idempotency_key,
            request=request,
            response=response,
            status=status,
            latency_ms=latency_ms,
            created_at=now,
        )


class UsageRecordRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        *,
        model_call_id: str | None = None,
        run_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        usage_id: str | None = None,
    ) -> UsageRecord:
        uid = usage_id or str(uuid4())
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO usage_record (id, model_call_id, run_id, input_tokens, "
                    "output_tokens, cache_tokens, reasoning_tokens, estimated_cost_usd, "
                    "created_at) "
                    "VALUES (:id, :model_call_id, :run_id, :input_tokens, :output_tokens, "
                    ":cache_tokens, :reasoning_tokens, :estimated_cost_usd, :created_at)"
                ),
                {
                    "id": uid,
                    "model_call_id": model_call_id,
                    "run_id": run_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_tokens": cache_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "estimated_cost_usd": estimated_cost_usd,
                    "created_at": iso_now(),
                },
            )
        return UsageRecord(
            id=uid,
            model_call_id=model_call_id,
            run_id=run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            reasoning_tokens=reasoning_tokens,
            estimated_cost_usd=estimated_cost_usd,
            created_at=now,
        )


def _artifact_from_row(row: Any) -> Artifact:
    return Artifact(
        id=row["id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        media_type=row["media_type"],
        path_or_name=row["path_or_name"],
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        idempotency_key=row["idempotency_key"],
        created_at=parse_dt(row["created_at"]) or utcnow(),
        has_data=bool(row["has_data"]),
    )


def _model_call_from_row(row: Any) -> ModelCall:
    return ModelCall(
        id=row["id"],
        run_id=row["run_id"],
        task_id=row["task_id"],
        role=row["role"],
        provider=row["provider"],
        model_id=row["model_id"],
        idempotency_key=row["idempotency_key"],
        request=loads_json(row["request_json"]) if row["request_json"] else None,
        response=loads_json(row["response_json"]) if row["response_json"] else None,
        status=row["status"],
        latency_ms=row["latency_ms"],
        created_at=parse_dt(row["created_at"]) or utcnow(),
    )
