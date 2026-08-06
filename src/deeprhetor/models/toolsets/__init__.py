"""Role-specific Pydantic AI toolsets."""

from __future__ import annotations

from deeprhetor.models.roles import RoleName
from deeprhetor.models.toolsets.critic import build_critic_toolset
from deeprhetor.models.toolsets.outline_editor import build_outline_editor_toolset
from deeprhetor.models.toolsets.supervisor import build_supervisor_toolset
from deeprhetor.models.toolsets.topic_worker import build_topic_worker_toolset
from deeprhetor.models.toolsets.verifier import build_verifier_toolset
from deeprhetor.models.toolsets.writer import build_writer_toolset
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps

_BUILDERS = {
    RoleName.SUPERVISOR: build_supervisor_toolset,
    RoleName.TOPIC_WORKER: build_topic_worker_toolset,
    RoleName.VERIFIER: build_verifier_toolset,
    RoleName.COVERAGE_CRITIC: build_critic_toolset,
    RoleName.OUTLINE_EDITOR: build_outline_editor_toolset,
    RoleName.WRITER: build_writer_toolset,
}


def toolset_for_role(role: str) -> FunctionToolset[AgentDeps]:
    builder = _BUILDERS.get(role)  # type: ignore[arg-type]
    if builder is None:
        raise KeyError(f"Unknown role: {role}")
    return builder()


__all__ = [
    "build_critic_toolset",
    "build_outline_editor_toolset",
    "build_supervisor_toolset",
    "build_topic_worker_toolset",
    "build_verifier_toolset",
    "build_writer_toolset",
    "toolset_for_role",
]
