"""Async and sync SQLite engine helpers."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SCHEMA_VERSION = 2


def sqlite_url(path: Path | str, *, async_: bool = False) -> str:
    resolved = Path(path).resolve().as_posix()
    if async_:
        return f"sqlite+aiosqlite:///{resolved}"
    return f"sqlite:///{resolved}"


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def ensure_sqlite_pragmas(engine: Engine | AsyncEngine) -> None:
    """Register connection hooks that enable FKs and WAL for SQLite."""
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    event.listen(sync_engine, "connect", _set_sqlite_pragma)


def create_sync_engine_for_path(path: Path | str) -> Engine:
    engine = create_engine(sqlite_url(path, async_=False), future=True)
    ensure_sqlite_pragmas(engine)
    return engine


def create_async_engine_for_path(path: Path | str) -> AsyncEngine:
    engine = create_async_engine(sqlite_url(path, async_=True), future=True)
    ensure_sqlite_pragmas(engine)
    return engine


def apply_migrations(path: Path | str, *, revision: str = "head") -> None:
    """Apply Alembic migrations to a SQLite project file."""
    from alembic import command
    from alembic.config import Config

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # alembic.ini lives at repo root when installed editable.
    root = Path(__file__).resolve().parents[3]
    ini = root / "alembic.ini"
    if not ini.is_file():
        raise FileNotFoundError(f"alembic.ini not found at {ini}")

    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path, async_=False))
    command.upgrade(cfg, revision)

    engine = create_sync_engine_for_path(db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_version (version, applied_at) "
                "VALUES (:v, datetime('now')) "
                "ON CONFLICT(version) DO NOTHING"
            ),
            {"v": SCHEMA_VERSION},
        )
    engine.dispose()
