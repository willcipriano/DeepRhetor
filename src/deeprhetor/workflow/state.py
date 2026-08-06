"""Thin LangGraph workflow state — IDs and control fields only."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

WorkflowStage = Literal[
    "created",
    "planning",
    "awaiting_plan_approval",
    "revising_plan",
    "dispatching",
    "working",
    "joined",
    "completed",
]

ApprovalAction = Literal["approve", "feedback"]


class WorkflowState(TypedDict):
    """Graph state: no corpus, plan bodies, or claim inventories."""

    project_id: str
    run_id: str
    plan_version: int
    task_ids: list[str]
    stage: WorkflowStage
    # Interrupt / approval control (populated around human_plan_approval).
    approval_action: NotRequired[ApprovalAction | None]
    plan_feedback: NotRequired[str | None]
    # Opaque plan row id only — never the full ResearchPlan payload.
    plan_id: NotRequired[str | None]
