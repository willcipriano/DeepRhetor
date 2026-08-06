"""Thin LangGraph-oriented checkpoint helper using the domain ``checkpoint`` table.

Domain tables remain authoritative. This stores opaque JSON payloads under a
namespace prefix so LangGraph-style checkpoints can share the project SQLite
file without adopting a separate checkpoint package schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.repositories.base import dumps_json, iso_now, loads_json, parse_dt, utcnow

DEFAULT_NAMESPACE_PREFIX = "lg:"


class CheckpointRecord(BaseModel):
    id: str
    run_id: str
    node_name: str
    checkpoint_ns: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CheckpointStore:
    """Minimal put/get/list against ``checkpoint`` with an optional namespace prefix."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        namespace_prefix: str = DEFAULT_NAMESPACE_PREFIX,
    ) -> None:
        self._engine = engine
        self._prefix = namespace_prefix

    def namespaced(self, namespace: str) -> str:
        if namespace.startswith(self._prefix):
            return namespace
        return f"{self._prefix}{namespace}"

    async def put(
        self,
        *,
        run_id: str,
        node_name: str,
        payload: dict[str, Any],
        namespace: str = "",
        checkpoint_id: str | None = None,
    ) -> CheckpointRecord:
        cid = checkpoint_id or str(uuid4())
        ns = self.namespaced(namespace)
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO checkpoint "
                    "(id, run_id, node_name, checkpoint_ns, payload_json, created_at) "
                    "VALUES (:id, :run_id, :node_name, :checkpoint_ns, :payload_json, :created_at)"
                ),
                {
                    "id": cid,
                    "run_id": run_id,
                    "node_name": node_name,
                    "checkpoint_ns": ns,
                    "payload_json": dumps_json(payload),
                    "created_at": iso_now(),
                },
            )
        return CheckpointRecord(
            id=cid,
            run_id=run_id,
            node_name=node_name,
            checkpoint_ns=ns,
            payload=payload,
            created_at=now,
        )

    async def latest(
        self, *, run_id: str, node_name: str | None = None, namespace: str = ""
    ) -> CheckpointRecord | None:
        ns = self.namespaced(namespace)
        clauses = ["run_id = :run_id", "checkpoint_ns = :checkpoint_ns"]
        params: dict[str, Any] = {"run_id": run_id, "checkpoint_ns": ns}
        if node_name is not None:
            clauses.append("node_name = :node_name")
            params["node_name"] = node_name
        sql = (
            "SELECT id, run_id, node_name, checkpoint_ns, payload_json, created_at "
            f"FROM checkpoint WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT 1"
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            row = result.mappings().first()
        if row is None:
            return None
        return CheckpointRecord(
            id=row["id"],
            run_id=row["run_id"],
            node_name=row["node_name"],
            checkpoint_ns=row["checkpoint_ns"],
            payload=loads_json(row["payload_json"]),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    async def list_for_run(self, run_id: str, *, namespace: str | None = None) -> list[CheckpointRecord]:
        clauses = ["run_id = :run_id"]
        params: dict[str, Any] = {"run_id": run_id}
        if namespace is not None:
            clauses.append("checkpoint_ns = :checkpoint_ns")
            params["checkpoint_ns"] = self.namespaced(namespace)
        else:
            clauses.append("checkpoint_ns LIKE :ns_like")
            params["ns_like"] = f"{self._prefix}%"
        sql = (
            "SELECT id, run_id, node_name, checkpoint_ns, payload_json, created_at "
            f"FROM checkpoint WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at ASC"
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            rows = result.mappings().all()
        return [
            CheckpointRecord(
                id=row["id"],
                run_id=row["run_id"],
                node_name=row["node_name"],
                checkpoint_ns=row["checkpoint_ns"],
                payload=loads_json(row["payload_json"]),
                created_at=parse_dt(row["created_at"]) or utcnow(),
            )
            for row in rows
        ]
