"""Base repository helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from deeprhetor.db import create_async_engine_for_path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().replace(microsecond=0).isoformat()


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def dumps_json(value: Any) -> str:
    return json.dumps(value, default=str)


def loads_json(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return {} if default is None else default
    return json.loads(value)


class BaseRepository:
    """Thin async repository bound to a project SQLite file."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @classmethod
    def from_path(cls, path: Path | str) -> BaseRepository:
        return cls(create_async_engine_for_path(path))

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            yield conn

    async def dispose(self) -> None:
        await self._engine.dispose()


def row_mapping(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row
