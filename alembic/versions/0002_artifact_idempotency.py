"""Add artifact idempotency_key for replay-safe creates.

Revision ID: 0002_artifact_idempotency
Revises: 0001_initial
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_artifact_idempotency"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("PRAGMA foreign_keys=ON")
    op.execute("ALTER TABLE artifact ADD COLUMN idempotency_key TEXT")
    # SQLite UNIQUE allows multiple NULLs; replayed creates share a non-null key.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_idempotency_key "
        "ON artifact(idempotency_key)"
    )
    op.execute(
        "INSERT INTO schema_version (version, applied_at) "
        "VALUES (2, datetime('now')) "
        "ON CONFLICT(version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_artifact_idempotency_key")
    # SQLite cannot DROP COLUMN without table rebuild; leave column in place.
    op.execute("DELETE FROM schema_version WHERE version = 2")
