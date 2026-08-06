"""Supervisor role toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.toolsets._common import require_tasks


def build_supervisor_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="supervisor")

    @ts.tool
    async def list_provider_capabilities(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """List known source-provider capability descriptors (stub until Stage 3)."""
        plugins = list(ctx.deps.search_plugins.keys())
        return {
            "providers": [
                {
                    "name": name,
                    "configured": True,
                    "source_classes": ["unknown"],
                }
                for name in plugins
            ]
            or [
                {"name": "mediawiki", "configured": False, "note": "Stage 3"},
                {"name": "tavily", "configured": False, "note": "Stage 6"},
                {"name": "openalex", "configured": False, "note": "Stage 6"},
                {"name": "crossref", "configured": False, "note": "Stage 6"},
                {"name": "arxiv", "configured": False, "note": "Stage 6"},
                {"name": "local_files", "configured": False, "note": "Stage 3"},
            ]
        }

    @ts.tool
    async def read_project_brief(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the project's authoritative prompt and metadata."""
        if ctx.deps.projects is None:
            prompt = ctx.deps.scratch.get("prompt")
            if prompt is not None:
                return {
                    "ok": True,
                    "project_id": ctx.deps.project_id,
                    "prompt": prompt,
                    "source": "scratch",
                }
            return {
                "ok": False,
                "error": "not_configured",
                "message": "AgentDeps.projects repository is not configured",
            }
        project = await ctx.deps.projects.get(ctx.deps.project_id)
        if project is None:
            return {"ok": False, "error": "project_not_found", "project_id": ctx.deps.project_id}
        return {
            "ok": True,
            "project_id": project.id,
            "title": project.title,
            "prompt": project.prompt,
            "status": project.status,
            "metadata": project.metadata,
        }

    @ts.tool
    async def create_research_plan(
        ctx: RunContext[AgentDeps],
        plan_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a draft research plan into scratch (DB planning repos arrive later)."""
        ctx.deps.scratch["research_plan"] = plan_json
        return {"ok": True, "stored": "scratch.research_plan", "plan": plan_json}

    @ts.tool
    async def create_worker_assignment(
        ctx: RunContext[AgentDeps],
        assignment_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a worker assignment task when a task repository is available."""
        assignments = ctx.deps.scratch.setdefault("worker_assignments", [])
        assignments.append(assignment_json)
        task_id: str | None = None
        if ctx.deps.tasks is not None and ctx.deps.run_id is not None:
            tasks = require_tasks(ctx.deps)
            task = await tasks.create(
                run_id=ctx.deps.run_id,
                kind="topic_worker",
                assignment=assignment_json,
                idempotency_key=assignment_json.get("idempotency_key"),
            )
            task_id = task.id
        return {"ok": True, "task_id": task_id, "assignment": assignment_json}

    @ts.tool
    async def set_task_dependencies(
        ctx: RunContext[AgentDeps],
        task_id: str,
        depends_on_task_ids: list[str],
    ) -> dict[str, Any]:
        """Record task dependency edges."""
        if ctx.deps.tasks is None:
            return {
                "ok": False,
                "error": "not_configured",
                "message": "AgentDeps.tasks repository is not configured",
            }
        for dep in depends_on_task_ids:
            await ctx.deps.tasks.add_dependency(task_id, dep)
        return {"ok": True, "task_id": task_id, "depends_on": depends_on_task_ids}

    @ts.tool
    async def inspect_topic_progress(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Summarize task progress for the current run."""
        if ctx.deps.tasks is None or ctx.deps.run_id is None:
            return {
                "ok": True,
                "tasks": [],
                "note": "No run/task repository bound; returning empty progress.",
            }
        tasks = await ctx.deps.tasks.list_for_run(ctx.deps.run_id)
        return {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "kind": t.kind,
                    "status": str(t.status),
                    "attempt": t.attempt,
                }
                for t in tasks
            ],
        }

    @ts.tool
    async def inspect_coverage_summary(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Return the latest coverage report from scratch, if any."""
        report = ctx.deps.scratch.get("coverage_report")
        return {"ok": True, "coverage_report": report}

    @ts.tool
    async def request_gap_research(
        ctx: RunContext[AgentDeps],
        gap_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Queue a focused gap-research request for dispatch."""
        gaps = ctx.deps.scratch.setdefault("gap_requests", [])
        gaps.append(gap_json)
        return {"ok": True, "gap": gap_json}

    @ts.tool
    async def finalize_research(ctx: RunContext[AgentDeps], notes: str = "") -> dict[str, Any]:
        """Mark research finalization intent (workflow advances in Stage 5)."""
        ctx.deps.scratch["research_finalized"] = {"notes": notes}
        return {"ok": True, "finalized": True, "notes": notes}

    return ts
