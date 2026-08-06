"""Deterministic application services (fetch, parse, validate, render, persist)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .acquisition import AcquisitionPipeline, AcquisitionResult
from .checkpoint import CheckpointRecord, CheckpointStore
from .citation_validate import CitationValidator
from .critic import CoverageCriticService, CriticLoopState, CriticPassResult
from .fetch import (
    DEFAULT_MAX_BYTES,
    DEFAULT_USER_AGENT,
    FetchError,
    SSRFBlockedError,
    SecureHttpFetcher,
    validate_public_url,
)
from .fts import ClaimFtsHit, DocumentFtsHit, FtsService
from .latex import CompileResult, LatexRenderer, RenderedLatex, toolchain_ready
from .local_import import LocalFileImporter
from .outline import OutlineBuilderService
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
from .publish import PublicationService
from .recovery import RecoveryReport, RecoveryService, mark_orphaned_in_progress
from .scan import BatchScanResult, ScanService
from .verify import QuoteCheckResult, VerifierService
from .writer import WriterService

if TYPE_CHECKING:
    from .mediawiki_import import MediaWikiImporter as MediaWikiImporter


def __getattr__(name: str) -> Any:
    # Lazy: mediawiki_import imports plugins.mediawiki, which imports services.fetch.
    if name == "MediaWikiImporter":
        from .mediawiki_import import MediaWikiImporter as _MediaWikiImporter

        return _MediaWikiImporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AcquisitionPipeline",
    "AcquisitionResult",
    "BatchScanResult",
    "CheckpointRecord",
    "CheckpointStore",
    "CitationValidator",
    "ClaimFtsHit",
    "CompileResult",
    "CoverageCriticService",
    "CriticLoopState",
    "CriticPassResult",
    "DEFAULT_EXTENSION",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_USER_AGENT",
    "DocumentFtsHit",
    "FetchError",
    "FtsService",
    "LatexRenderer",
    "LocalFileImporter",
    "MediaWikiImporter",
    "OpenProject",
    "OutlineBuilderService",
    "PREFERRED_EXTENSIONS",
    "PublicationService",
    "QuoteCheckResult",
    "RecoveryReport",
    "RecoveryService",
    "RenderedLatex",
    "SSRFBlockedError",
    "ScanService",
    "SecureHttpFetcher",
    "VerifierService",
    "WriterService",
    "backup_project",
    "create_project",
    "create_project_async",
    "mark_orphaned_in_progress",
    "normalize_project_path",
    "open_project",
    "open_project_async",
    "toolchain_ready",
    "validate_public_url",
]
