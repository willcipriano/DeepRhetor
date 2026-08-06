"""Typed dependencies injected into Pydantic AI agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.operations import (
    EventRepository,
    ModelCallRepository,
    UsageRecordRepository,
)
from deeprhetor.repositories.project import ProjectRepository
from deeprhetor.repositories.workflow import RunRepository, TaskRepository
from deeprhetor.services.fts import FtsService


class SearchPlugin(Protocol):
    """Minimal Stage-3 search plugin surface (optional until plugins land)."""

    async def search(self, query: str, **kwargs: Any) -> Any: ...


class FetchPlugin(Protocol):
    """Minimal Stage-3 fetch plugin surface (optional until plugins land)."""

    async def fetch(self, url: str, **kwargs: Any) -> Any: ...


@dataclass
class AgentDeps:
    """Repository/service bag for role tools — never raw SQL."""

    project_id: str
    run_id: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    projects: ProjectRepository | None = None
    runs: RunRepository | None = None
    tasks: TaskRepository | None = None
    documents: DocumentRepository | None = None
    model_calls: ModelCallRepository | None = None
    usage_records: UsageRecordRepository | None = None
    events: EventRepository | None = None
    fts: FtsService | None = None
    # Stage 3 plugins — optional; search/fetch tools error clearly when absent.
    search_plugins: dict[str, SearchPlugin] = field(default_factory=dict)
    fetch_plugins: dict[str, FetchPlugin] = field(default_factory=dict)
    # Scratch space for in-memory stubs until knowledge repos land.
    scratch: dict[str, Any] = field(default_factory=dict)
