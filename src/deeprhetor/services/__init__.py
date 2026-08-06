"""Deterministic application services (fetch, parse, validate, render, persist)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .checkpoint import CheckpointRecord, CheckpointStore
from .fetch import (
    DEFAULT_MAX_BYTES,
    DEFAULT_USER_AGENT,
    FetchError,
    SSRFBlockedError,
    SecureHttpFetcher,
    validate_public_url,
)
from .fts import ClaimFtsHit, DocumentFtsHit, FtsService
from .local_import import LocalFileImporter
from .project_store import (
    DEFAULT_EXTENSION,
    PREFERRED_EXTENSIONS,
    OpenProject,
    backup_project,
    create_project,
    create_project_async,
    normalize_project_path,
    open_project,
    open_project_async,
)
from .recovery import RecoveryReport, RecoveryService, mark_orphaned_in_progress

if TYPE_CHECKING:
    from .mediawiki_import import MediaWikiImporter as MediaWikiImporter


def __getattr__(name: str) -> Any:
    # Lazy: mediawiki_import imports plugins.mediawiki, which imports services.fetch.
    if name == "MediaWikiImporter":
        from .mediawiki_import import MediaWikiImporter as _MediaWikiImporter

        return _MediaWikiImporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CheckpointRecord",
    "CheckpointStore",
    "ClaimFtsHit",
    "DEFAULT_EXTENSION",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_USER_AGENT",
    "DocumentFtsHit",
    "FetchError",
    "FtsService",
    "LocalFileImporter",
    "MediaWikiImporter",
    "OpenProject",
    "PREFERRED_EXTENSIONS",
    "RecoveryReport",
    "RecoveryService",
    "SSRFBlockedError",
    "SecureHttpFetcher",
    "backup_project",
    "create_project",
    "create_project_async",
    "mark_orphaned_in_progress",
    "normalize_project_path",
    "open_project",
    "open_project_async",
    "validate_public_url",
]
