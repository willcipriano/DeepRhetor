"""High-level helpers to run and resume the Stage 5 workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import RunStatus
from deeprhetor.plugins.registry import SearchProviderRegistry
from deeprhetor.repositories.base import utcnow
from deeprhetor.repositories.workflow import Run
from deeprhetor.workflow.agents import SupervisorAgent, TopicWorkerAgent
from deeprhetor.workflow.checkpointer import ProjectSqliteSaver, thread_config
from deeprhetor.workflow.context import WorkflowContext
from deeprhetor.workflow.graph import build_workflow_graph
from deeprhetor.workflow.state import WorkflowState


@dataclass
class WorkflowHandle:
    """Compiled graph plus durable checkpointer for a single run."""

    ctx: WorkflowContext
    checkpointer: ProjectSqliteSaver
    graph: Any
    run: Run

    @property
    def config(self) -> dict[str, Any]:
        return thread_config(self.run.id)

    async def astream(self, input_or_command: Any) -> list[Any]:
        chunks: list[Any] = []
        async for chunk in self.graph.astream(
            input_or_command, self.config, stream_mode="updates"
        ):
            chunks.append(chunk)
        return chunks

    async def ainvoke(self, input_or_command: Any) -> Any:
        return await self.graph.ainvoke(input_or_command, self.config)

    async def get_state(self) -> Any:
        return await self.graph.aget_state(self.config)


async def open_workflow(
    engine: AsyncEngine,
    *,
    project_id: str,
    run_id: str | None = None,
    configuration_snapshot_id: str | None = None,
    supervisor: SupervisorAgent | None = None,
    worker: TopicWorkerAgent | None = None,
    critic: Any | None = None,
    proposer: Any | None = None,
    providers: SearchProviderRegistry | None = None,
    limits: Any | None = None,
) -> WorkflowHandle:
    """Create or resume a run-bound compiled workflow."""
    ctx = WorkflowContext.from_engine(
        engine,
        supervisor=supervisor,
        worker=worker,
        critic=critic,
        proposer=proposer,
        providers=providers,
        limits=limits,
        configuration_snapshot_id=configuration_snapshot_id,
    )
    if run_id:
        run = await ctx.runs.get(run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
    else:
        run = await ctx.runs.create(
            project_id=project_id,
            configuration_snapshot_id=configuration_snapshot_id,
            status=RunStatus.CREATED,
        )
        await ctx.runs.update_status(run.id, RunStatus.CREATED, started_at=utcnow())

    checkpointer = ProjectSqliteSaver(engine, run_id=run.id)
    graph = build_workflow_graph(ctx).compile(checkpointer=checkpointer)
    return WorkflowHandle(ctx=ctx, checkpointer=checkpointer, graph=graph, run=run)


def initial_state(*, project_id: str, run_id: str) -> WorkflowState:
    return WorkflowState(
        project_id=project_id,
        run_id=run_id,
        plan_version=0,
        task_ids=[],
        stage="created",
        approval_action=None,
        plan_feedback=None,
        plan_id=None,
    )


async def start_until_plan_interrupt(handle: WorkflowHandle) -> list[Any]:
    """Run from create_project through human_plan_approval interrupt."""
    return await handle.astream(
        initial_state(project_id=handle.run.project_id, run_id=handle.run.id)
    )


async def resume_with_approval(
    handle: WorkflowHandle,
    *,
    action: str,
    feedback: str | None = None,
) -> list[Any]:
    """Resume after plan interrupt with approve or feedback."""
    payload: dict[str, Any] = {"action": action}
    if feedback is not None:
        payload["feedback"] = feedback
    return await handle.astream(Command(resume=payload))


__all__ = [
    "WorkflowHandle",
    "initial_state",
    "open_workflow",
    "resume_with_approval",
    "start_until_plan_interrupt",
]
