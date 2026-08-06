"""Pydantic AI model registry, role agents, and scoped toolsets (Stage 4)."""

from __future__ import annotations

from deeprhetor.models.agents import (
    ROLE_INSTRUCTIONS,
    ROLE_OUTPUT_TYPES,
    agent_tool_names,
    assert_role_tool_surface,
    build_all_role_agents,
    build_role_agent,
)
from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.registry import (
    DEFAULT_PRESET_IDS,
    DeclaredCapabilities,
    ModelCapabilityError,
    ModelCapabilityRequirements,
    ModelRegistry,
    ResolvedModel,
)
from deeprhetor.models.roles import (
    CRITIC_TOOLS,
    OUTLINE_EDITOR_TOOLS,
    ROLE_PRESET,
    ROLE_TOOLS,
    SUPERVISOR_TOOLS,
    TOPIC_WORKER_TOOLS,
    VERIFIER_TOOLS,
    WRITER_FORBIDDEN_TOOL_PATTERNS,
    WRITER_TOOLS,
    RoleName,
    tool_names_for_role,
)
from deeprhetor.models.usage import RecordedRun, run_agent_recorded, run_with_usage

__all__ = [
    "CRITIC_TOOLS",
    "DEFAULT_PRESET_IDS",
    "DeclaredCapabilities",
    "ModelCapabilityError",
    "ModelCapabilityRequirements",
    "ModelRegistry",
    "OUTLINE_EDITOR_TOOLS",
    "ROLE_INSTRUCTIONS",
    "ROLE_OUTPUT_TYPES",
    "ROLE_PRESET",
    "ROLE_TOOLS",
    "RecordedRun",
    "ResolvedModel",
    "RoleName",
    "SUPERVISOR_TOOLS",
    "TOPIC_WORKER_TOOLS",
    "VERIFIER_TOOLS",
    "WRITER_FORBIDDEN_TOOL_PATTERNS",
    "WRITER_TOOLS",
    "AgentDeps",
    "agent_tool_names",
    "assert_role_tool_surface",
    "build_all_role_agents",
    "build_role_agent",
    "run_agent_recorded",
    "run_with_usage",
    "tool_names_for_role",
]
