"""Shared helpers for model tool implementations."""

from __future__ import annotations

from typing import Any


def plugin_not_configured(kind: str, name: str | None = None) -> dict[str, Any]:
    """Clear error payload when Stage 3 search/fetch plugins are absent."""
    target = f"{kind}:{name}" if name else kind
    return {
        "ok": False,
        "error": "not_configured",
        "message": (
            f"{target} is not configured. "
            "Search/fetch plugins land in Stage 3; wire AgentDeps.search_plugins "
            "/ fetch_plugins before calling this tool."
        ),
    }


def require_projects(deps: Any) -> Any:
    if deps.projects is None:
        raise RuntimeError("AgentDeps.projects repository is not configured")
    return deps.projects


def require_tasks(deps: Any) -> Any:
    if deps.tasks is None:
        raise RuntimeError("AgentDeps.tasks repository is not configured")
    return deps.tasks
