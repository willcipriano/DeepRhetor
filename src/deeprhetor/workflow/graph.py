"""Thin LangGraph StateGraph: plan → approve → capability-aware fan-out."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from deeprhetor.domain.enums import ClaimStatus, PlanStatus, RunStatus, TaskStatus
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
        critic_pass = int(state.get("critic_pass") or 0)
        for assignment in assignments:
            key = assignment_idempotency_key(
                run_id=run_id,
                plan_version=state["plan_version"],
                topic_id=assignment.topic_id,
                provider=assignment.provider_or_class,
            )
            # Distinct keys per critic pass so gap re-dispatch is resumable but not a no-op.
            if critic_pass:
                key = f"{key}:critic{critic_pass}"
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
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="join_and_progress",
            payload={
                "stage": "joined",
                "completed": completed,
                "total": len(worker_tasks),
            },
            namespace="nodes",
        )
        return {"stage": "joined"}

    async def scan_documents(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        docs = await ctx.documents.list_for_project(state["project_id"])
        document_ids: list[str] = []
        for doc in docs:
            version = await ctx.documents.latest_version(doc.id)
            if version is None:
                continue
            document_ids.append(doc.id)
            await ctx.scan_service.scan_until_complete(
                document_id=doc.id,
                document_version_id=version.id,
            )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.scan",
            message=f"Scanned {len(document_ids)} documents",
            payload={"document_ids": document_ids},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="scan_documents",
            payload={"stage": "scanning", "document_ids": document_ids},
            namespace="nodes",
        )
        return {"stage": "scanning", "document_ids": document_ids}

    async def propose_claims(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        plan_id = state.get("plan_id")
        if not plan_id:
            raise ValueError("plan_id required before propose_claims")
        stored = await ctx.plans.get(plan_id)
        if stored is None:
            raise ValueError(f"plan not found: {plan_id}")
        claim_ids = await ctx.proposer.propose_for_plan(
            project_id=state["project_id"],
            run_id=run_id,
            plan=stored.plan,
            documents=ctx.documents,
            claims=ctx.claims,
            evidence=ctx.evidence,
        )
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.propose",
            message=f"Proposed {len(claim_ids)} claims",
            payload={"claim_ids": claim_ids},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="propose_claims",
            payload={"stage": "proposing", "claim_ids": claim_ids},
            namespace="nodes",
        )
        return {"stage": "proposing", "claim_ids": claim_ids}

    async def verify_claims(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        proposed = await ctx.claims.list_for_project(
            state["project_id"], status=ClaimStatus.PROPOSED, run_id=run_id
        )
        decisions: list[str] = []
        for row in proposed:
            decision = await ctx.verifier.verify_claim(row.id)
            decisions.append(str(decision.decision))
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.verify",
            message=f"Verified {len(proposed)} proposed claims",
            payload={"count": len(proposed), "decisions": decisions},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="verify_claims",
            payload={"stage": "verifying", "count": len(proposed)},
            namespace="nodes",
        )
        return {"stage": "verifying"}

    async def coverage_critic(state: WorkflowState) -> dict[str, Any]:
        run_id = state["run_id"]
        plan_id = state.get("plan_id")
        if not plan_id:
            raise ValueError("plan_id required before coverage_critic")
        stored = await ctx.plans.get(plan_id)
        if stored is None:
            raise ValueError(f"plan not found: {plan_id}")

        result = await ctx.critic.judge(
            plan=stored.plan,
            project_id=state["project_id"],
            plan_id=plan_id,
            run_id=run_id,
            state=ctx.critic_loop,
            critic_service=ctx.critic_service,
        )
        ctx.critic_loop = result.state
        gap_ids = [g.gap_id for g in result.report.gaps]
        research_complete = result.state.is_complete or not result.should_continue

        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.critic",
            message=(
                "Coverage complete"
                if research_complete and result.state.is_complete
                else f"Coverage gaps: {len(gap_ids)} (pass {result.state.pass_count})"
            ),
            payload={
                "pass_count": result.state.pass_count,
                "is_complete": result.state.is_complete,
                "should_continue": result.should_continue,
                "terminated_reason": result.state.terminated_reason,
                "gap_request_ids": gap_ids,
            },
        )

        if research_complete:
            await ctx.runs.update_status(run_id, RunStatus.COMPLETED)
            await ctx.checkpoints.put(
                run_id=run_id,
                node_name="coverage_critic",
                payload={
                    "stage": "completed",
                    "pass_count": result.state.pass_count,
                    "terminated_reason": result.state.terminated_reason,
                },
                namespace="nodes",
            )
            await _emit(
                ctx,
                run_id=run_id,
                kind="workflow.completed",
                message="Knowledge loop completed (scan → verify → critic gate)",
                payload={
                    "stage": "completed",
                    "terminated_reason": result.state.terminated_reason,
                },
            )
            return {
                "stage": "completed",
                "critic_pass": result.state.pass_count,
                "research_complete": True,
                "gap_request_ids": gap_ids,
            }

        # Gaps → supervisor path: emit gap requests and re-dispatch (critic never fans out).
        await _emit(
            ctx,
            run_id=run_id,
            kind="workflow.gap_requests",
            message=f"Critic emitted {len(gap_ids)} gap requests for supervisor",
            payload={"gap_request_ids": gap_ids, "gaps": [g.model_dump(mode="json") for g in result.report.gaps]},
        )
        await ctx.checkpoints.put(
            run_id=run_id,
            node_name="coverage_critic",
            payload={
                "stage": "criticizing",
                "pass_count": result.state.pass_count,
                "gap_request_ids": gap_ids,
            },
            namespace="nodes",
        )
        return {
            "stage": "criticizing",
            "critic_pass": result.state.pass_count,
            "research_complete": False,
            "gap_request_ids": gap_ids,
        }

    def route_after_critic(
        state: WorkflowState,
    ) -> Literal["dispatch_workers", "__end__"]:
        if state.get("research_complete"):
            return "__end__"
        return "dispatch_workers"

    graph = StateGraph(WorkflowState)
    graph.add_node("create_project", create_project)
    graph.add_node("supervisor_plan", supervisor_plan)
    graph.add_node("human_plan_approval", human_plan_approval)
    graph.add_node("dispatch_workers", dispatch_workers)
    graph.add_node("topic_worker_fanout", topic_worker_fanout)
    graph.add_node("join_and_progress", join_and_progress)
    graph.add_node("scan_documents", scan_documents)
    graph.add_node("propose_claims", propose_claims)
    graph.add_node("verify_claims", verify_claims)
    graph.add_node("coverage_critic", coverage_critic)

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
    graph.add_edge("join_and_progress", "scan_documents")
    graph.add_edge("scan_documents", "propose_claims")
    graph.add_edge("propose_claims", "verify_claims")
    graph.add_edge("verify_claims", "coverage_critic")
    graph.add_conditional_edges(
        "coverage_critic",
        route_after_critic,
        {
            "dispatch_workers": "dispatch_workers",
            "__end__": END,
        },
    )

    return graph


def compile_workflow(
    ctx: WorkflowContext,
    *,
    checkpointer: ProjectSqliteSaver | None = None,
) -> Any:
    """Build and compile the workflow with a project-SQLite checkpointer."""
    saver = checkpointer or ProjectSqliteSaver(ctx.engine, run_id="pending")
    return build_workflow_graph(ctx).compile(checkpointer=saver)
