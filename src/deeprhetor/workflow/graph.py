"""Thin LangGraph StateGraph: plan → approve → capability-aware fan-out."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from deeprhetor.domain.enums import PlanStatus, RunStatus, TaskStatus
from deeprhetor.domain.planning import ResearchPlan
from deeprhetor.repositories.base import utcnow
from deeprhetor.workflow.checkpointer import ProjectSqliteSaver
from deeprhetor.workflow.context import WorkflowContext
from deeprhetor.workflow.dispatch import (
    assignment_idempotency_key,
    build_capability_aware_assignments,
)
from deeprhetor.workflow.state import ApprovalAction, WorkflowState


async def _emit(
    ctx: WorkflowContext,
    *,
    run_id: str,
    kind: str,
    message: str,
    payload: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> None:
    await ctx.events.create(
        kind=kind,
        message=message,
        run_id=run_id,
        task_id=task_id,
        payload=payload or {},
    )


def build_workflow_graph(ctx: WorkflowContext) -> Any:
    """Compile the Stage 5 workflow graph closed over ``ctx``."""

    async def create_project(state: WorkflowState) -> dict[str, Any]:
        project = await ctx.projects.get(state["project_id"])
        if project is None:
            raise ValueError(f"project not found: {state['project_id']}")

        run_id = state.get("run_id") or ""
        if run_id:
            run = await ctx.runs.get(run_id)
            if run is None:
                raise ValueError(f"run not found: {run_id}")
        else:
            run = await ctx.runs.create(
                project_id=project.id,
                configuration_snapshot_id=ctx.configuration_snapshot_id,
                status=RunStatus.CREATED,
                plan_version=state.get("plan_version") or None,
            )
            run_id = run.id

        await ctx.runs.update_status(
            run_id,
            RunStatus.CREATED,
            started_at=run.started_at or utcnow(),
        )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.stage",
            message="Project run ready for planning",
            payload={"stage": "created", "project_id": project.id},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="create_project",
            payload={"stage": "created", "project_id": project.id},
            namespace="nodes",
        )
        return {
            "project_id": project.id,
            "run_id": run_id,
            "plan_version": state.get("plan_version") or 0,
            "task_ids": list(state.get("task_ids") or []),
            "stage": "created",
            "approval_action": None,
            "plan_feedback": None,
            "plan_id": state.get("plan_id"),
        }

    async def supervisor_plan(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        project = await ctx.projects.get(state["project_id"])
        if project is None:
            raise ValueError(f"project not found: {state['project_id']}")

        feedback = state.get("plan_feedback")
        next_version = (state.get("plan_version") or 0) + 1
        plan = await ctx.supervisor.create_plan(
            project_id=project.id,
            prompt=project.prompt,
            feedback=feedback,
            version=next_version,
        )
        plan = plan.model_copy(
            update={
                "status": PlanStatus.AWAITING_APPROVAL,
                "version": next_version,
                "project_id": project.id,
                "prompt": project.prompt,
            }
        )
        stored = await ctx.plans.create(plan, run_id=run_id, supersede_previous=True)
        await ctx.runs.update_status(
            run_id,
            RunStatus.AWAITING_PLAN_APPROVAL,
            plan_version=stored.version,
        )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.plan_created",
            message=f"Research plan v{stored.version} awaiting approval",
            payload={
                "plan_id": stored.id,
                "plan_version": stored.version,
                "topic_count": len(stored.plan.topics),
            },
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="supervisor_plan",
            payload={
                "stage": "awaiting_plan_approval",
                "plan_id": stored.id,
                "plan_version": stored.version,
            },
            namespace="nodes",
        )
        return {
            "plan_id": stored.id,
            "plan_version": stored.version,
            "stage": "awaiting_plan_approval",
            "approval_action": None,
            "plan_feedback": None,
        }

    async def human_plan_approval(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        plan_id = state.get("plan_id")
        decision = interrupt(
            {
                "type": "plan_approval",
                "project_id": state["project_id"],
                "run_id": run_id,
                "plan_id": plan_id,
                "plan_version": state["plan_version"],
                "message": "Approve the research plan or return feedback",
            }
        )
        if not isinstance(decision, dict):
            raise TypeError("plan approval resume value must be a dict")

        action_raw = decision.get("action") or decision.get("approval_action")
        if action_raw not in {"approve", "feedback"}:
            raise ValueError("approval decision action must be 'approve' or 'feedback'")
        action: ApprovalAction = action_raw  # type: ignore[assignment]
        feedback = decision.get("feedback") or decision.get("plan_feedback")

        if action == "feedback":
            await _emit(
                ctx,
                run_id=run_id,
                kind="workflow.plan_feedback",
                message="Plan returned for revision",
                payload={"plan_id": plan_id, "feedback": feedback},
            )
            return {
                "approval_action": "feedback",
                "plan_feedback": feedback or "",
                "stage": "revising_plan",
            }

        # Approve — commit durable plan status before advancing.
        if plan_id:
            stored = await ctx.plans.get(plan_id)
            if stored is not None:
                approved_plan = stored.plan.model_copy(update={"status": PlanStatus.APPROVED})
                await ctx.plans.update_status(
                    plan_id,
                    PlanStatus.APPROVED,
                    approved_at=utcnow(),
                    plan=approved_plan,
                )
        await ctx.runs.update_status(
            run_id,
            RunStatus.RUNNING,
            plan_version=state["plan_version"],
            started_at=utcnow(),
        )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.plan_approved",
            message=f"Research plan v{state['plan_version']} approved",
            payload={"plan_id": plan_id, "plan_version": state["plan_version"]},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="human_plan_approval",
            payload={
                "stage": "dispatching",
                "plan_id": plan_id,
                "plan_version": state["plan_version"],
                "approved": True,
            },
            namespace="nodes",
        )
        return {
            "approval_action": "approve",
            "plan_feedback": None,
            "stage": "dispatching",
        }

    def route_after_approval(
        state: WorkflowState,
    ) -> Literal["supervisor_plan", "dispatch_workers"]:
        if state.get("approval_action") == "feedback":
            return "supervisor_plan"
        return "dispatch_workers"

    async def dispatch_workers(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        plan_id = state.get("plan_id")
        if not plan_id:
            raise ValueError("plan_id required before dispatch")
        stored = await ctx.plans.get(plan_id)
        if stored is None:
            raise ValueError(f"plan not found: {plan_id}")
        plan: ResearchPlan = stored.plan

        descriptors = ctx.providers.descriptors()
        assignments = build_capability_aware_assignments(plan, descriptors)
        if not assignments:
            await _emit(
                ctx,
                run_id=run_id,
                kind="workflow.dispatch_empty",
                message="No capability-matched assignments; nothing to fan out",
                payload={"plan_id": plan_id, "providers": [d.name for d in descriptors]},
            )

        task_ids: list[str] = []
        for assignment in assignments:
            key = assignment_idempotency_key(
                run_id=run_id,
                plan_version=state["plan_version"],
                topic_id=assignment.topic_id,
                provider=assignment.provider_or_class,
            )
            task = await ctx.tasks.create(
                run_id=run_id,
                kind="topic_worker",
                status=TaskStatus.PENDING,
                assignment=assignment.model_dump(mode="json"),
                idempotency_key=key,
            )
            task_ids.append(task.id)

        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.dispatched",
            message=f"Dispatched {len(task_ids)} capability-aware worker tasks",
            payload={
                "task_ids": task_ids,
                "assignment_count": len(assignments),
                "provider_count": len(descriptors),
            },
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="dispatch_workers",
            payload={"stage": "dispatching", "task_ids": task_ids},
            namespace="nodes",
        )
        return {"task_ids": task_ids, "stage": "dispatching"}

    async def topic_worker_fanout(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        task_ids = list(state.get("task_ids") or [])
        for task_id in task_ids:
            task = await ctx.tasks.get(task_id)
            if task is None:
                continue
            if task.status == TaskStatus.COMPLETED:
                # Replay-safe: already finished.
                continue
            await ctx.tasks.update_status(
                task_id, TaskStatus.RUNNING, started_at=utcnow()
            )
            assignment = task.assignment or {}
            result = await ctx.worker.acknowledge(assignment)
            await ctx.tasks.update_status(
                task_id, TaskStatus.COMPLETED, finished_at=utcnow()
            )
            await _emit(
                ctx,
                run_id=run_id,
                kind="workflow.worker_ack",
                message=f"Worker acknowledged task {task_id}",
                task_id=task_id,
                payload={"result": result},
            )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="topic_worker_fanout",
            payload={"stage": "working", "task_ids": task_ids},
            namespace="nodes",
        )
        return {"stage": "working"}

    async def join_and_progress(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        tasks = await ctx.tasks.list_for_run(run_id)
        worker_tasks = [t for t in tasks if t.kind == "topic_worker"]
        completed = sum(1 for t in worker_tasks if t.status == TaskStatus.COMPLETED)
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.progress",
            message=f"Joined fan-out: {completed}/{len(worker_tasks)} workers complete",
            payload={
                "completed": completed,
                "total": len(worker_tasks),
                "task_ids": [t.id for t in worker_tasks],
                "stage": "joined",
            },
        )
        # Stage 5 ends after join — not full knowledge/publication.
        await ctx.runs.update_status(run_id, RunStatus.COMPLETED)
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="join_and_progress",
            payload={
                "stage": "completed",
                "completed": completed,
                "total": len(worker_tasks),
            },
            namespace="nodes",
        )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.completed",
            message="Stage 5 workflow completed (plan → approve → fan-out → join)",
            payload={"stage": "completed"},
        )
        return {"stage": "completed"}

    graph = StateGraph(WorkflowState)
    graph.add_node("create_project", create_project)
    graph.add_node("supervisor_plan", supervisor_plan)
    graph.add_node("human_plan_approval", human_plan_approval)
    graph.add_node("dispatch_workers", dispatch_workers)
    graph.add_node("topic_worker_fanout", topic_worker_fanout)
    graph.add_node("join_and_progress", join_and_progress)

    graph.add_edge(START, "create_project")
    graph.add_edge("create_project", "supervisor_plan")
    graph.add_edge("supervisor_plan", "human_plan_approval")
    graph.add_conditional_edges(
        "human_plan_approval",
        route_after_approval,
        {
            "supervisor_plan": "supervisor_plan",
            "dispatch_workers": "dispatch_workers",
        },
    )
    graph.add_edge("dispatch_workers", "topic_worker_fanout")
    graph.add_edge("topic_worker_fanout", "join_and_progress")
    graph.add_edge("join_and_progress", END)

    return graph


def compile_workflow(
    ctx: WorkflowContext,
    *,
    checkpointer: ProjectSqliteSaver | None = None,
) -> Any:
    """Build and compile the workflow with a project-SQLite checkpointer."""
    saver = checkpointer or ProjectSqliteSaver(ctx.engine, run_id="pending")
    return build_workflow_graph(ctx).compile(checkpointer=saver)
