"""Role agent construction with typed outputs and scoped toolsets."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from deeprhetor.domain.knowledge import ProposedClaim, VerificationDecision
from deeprhetor.domain.planning import CoverageReport, ResearchPlan
from deeprhetor.domain.writing import Outline, StructuredDraft
from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import ROLE_TOOLS, RoleName, tool_names_for_role
from deeprhetor.models.toolsets import toolset_for_role

OutputT = TypeVar("OutputT", bound=BaseModel)

ROLE_OUTPUT_TYPES: dict[str, type[BaseModel]] = {
    RoleName.SUPERVISOR: ResearchPlan,
    RoleName.TOPIC_WORKER: ProposedClaim,
    RoleName.VERIFIER: VerificationDecision,
    RoleName.COVERAGE_CRITIC: CoverageReport,
    RoleName.OUTLINE_EDITOR: Outline,
    RoleName.WRITER: StructuredDraft,
}

ROLE_INSTRUCTIONS: dict[str, str] = {
    RoleName.SUPERVISOR: (
        "You are the DeepRhetor supervisor. Produce a faithful research plan from "
        "the user's prompt, decompose topics into simple worker assignments, and "
        "dispatch only capability-appropriate providers. Never fabricate sources."
    ),
    RoleName.TOPIC_WORKER: (
        "You are a cheap topic worker. Search one assigned angle, assess relevance, "
        "fully scan accepted documents, and propose claims with exact evidence. "
        "Stay within the assignment exclusions and limits."
    ),
    RoleName.VERIFIER: (
        "You are the claim verifier. Approve, reject, or request correction only "
        "after checking evidence against archived source spans. Do not invent quotes."
    ),
    RoleName.COVERAGE_CRITIC: (
        "You are the coverage critic. Judge whether approved claims cover the plan. "
        "Request focused gap research or mark research complete. Do not dispatch workers."
    ),
    RoleName.OUTLINE_EDITOR: (
        "You consolidate the approved plan and claim inventory into a writing outline. "
        "Do not search the web or approve claims."
    ),
    RoleName.WRITER: (
        "You are the frontier writer. Produce polished, non-redundant prose from the "
        "approved outline and claim inventory only. Never invent facts, sources, or "
        "citations. You have no web-search, fetch, or claim-approval tools."
    ),
}


def build_role_agent(
    role: str,
    *,
    model: Model | None = None,
    registry: ModelRegistry | None = None,
    use_test_model: bool = False,
    test_output_args: Any | None = None,
    instructions: str | None = None,
) -> Agent[AgentDeps, Any]:
    """Construct a Pydantic AI agent for a role with only that role's toolset."""
    if role not in ROLE_OUTPUT_TYPES:
        raise KeyError(f"Unknown role: {role}")

    output_type = ROLE_OUTPUT_TYPES[role]
    toolset = toolset_for_role(role)

    if model is None:
        if registry is not None:
            model = registry.build_model_for_role(
                role,
                test_output_args=test_output_args,
            )
        elif use_test_model:
            model = TestModel(
                custom_output_args=test_output_args,
                model_name=f"test:{role}",
            )
        else:
            raise ValueError("Provide model=, registry=, or use_test_model=True")

    agent: Agent[AgentDeps, Any] = Agent(
        model,
        deps_type=AgentDeps,
        output_type=output_type,
        instructions=instructions or ROLE_INSTRUCTIONS[role],
        toolsets=[toolset],
        name=role,
    )
    return agent


def build_all_role_agents(
    *,
    registry: ModelRegistry | None = None,
    use_test_model: bool = False,
    test_outputs: dict[str, Any] | None = None,
) -> dict[str, Agent[AgentDeps, Any]]:
    """Build the full Stage 4 role agent roster."""
    outputs = test_outputs or {}
    return {
        role: build_role_agent(
            role,
            registry=registry,
            use_test_model=use_test_model or (registry is not None and registry.uses_test_models),
            test_output_args=outputs.get(role),
        )
        for role in ROLE_OUTPUT_TYPES
    }


def agent_tool_names(agent: Agent[AgentDeps, Any]) -> frozenset[str]:
    """Return user tool names attached to an agent (excludes output tools)."""
    names: set[str] = set()
    for toolset in agent._user_toolsets or ():  # noqa: SLF001
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            names.update(tools.keys())
    return frozenset(names)


def assert_role_tool_surface(role: str, agent: Agent[AgentDeps, Any]) -> None:
    """Verify agent tools exactly match the closed role inventory."""
    expected = tool_names_for_role(role)
    actual = agent_tool_names(agent)
    if actual != expected:
        raise AssertionError(
            f"Role {role} tool surface mismatch: "
            f"extra={sorted(actual - expected)} missing={sorted(expected - actual)}"
        )


__all__ = [
    "ROLE_INSTRUCTIONS",
    "ROLE_OUTPUT_TYPES",
    "ROLE_TOOLS",
    "agent_tool_names",
    "assert_role_tool_surface",
    "build_all_role_agents",
    "build_role_agent",
]
