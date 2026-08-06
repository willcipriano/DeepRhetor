"""Base repository helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from deeprhetor.db import create_async_engine_for_path


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
