"""Stage 4 model registry, role agents, and tool-surface tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from deeprhetor.config import load_example_config
from deeprhetor.config.settings import AppConfig, ModelPreset
from deeprhetor.domain.knowledge import ProposedClaim, VerificationDecision
from deeprhetor.domain.planning import CoverageReport, ResearchPlan
from deeprhetor.domain.writing import Outline, StructuredDraft
from deeprhetor.models import (
    WRITER_FORBIDDEN_TOOL_PATTERNS,
    WRITER_TOOLS,
    AgentDeps,
    DeclaredCapabilities,
    ModelCapabilityError,
    ModelRegistry,
    ROLE_OUTPUT_TYPES,
    RoleName,
    agent_tool_names,
    assert_role_tool_surface,
    build_all_role_agents,
    build_role_agent,
    run_with_usage,
    tool_names_for_role,
)
from deeprhetor.models.registry import DEFAULT_PRESET_IDS
from deeprhetor.repositories.operations import ModelCallRepository, UsageRecordRepository
from deeprhetor.services.project_store import create_project_async


def test_registry_resolves_presets_from_example_config() -> None:
    cfg = load_example_config()
    registry = ModelRegistry(cfg)
    resolved = registry.resolve_presets()

    assert set(resolved) >= {"cheap", "mid", "frontier"}
    assert resolved["cheap"].model_id == "openai/gpt-4o-mini"
    assert resolved["mid"].model_id == "openai/gpt-4o"
    assert resolved["frontier"].model_id == "anthropic/claude-opus-4"
    assert resolved["cheap"].provider == "openrouter"

    snapshot = registry.snapshot_for_configuration()
    assert snapshot["frontier"]["model_id"] == "anthropic/claude-opus-4"
    assert snapshot["mid"]["preset"] == "mid"

    # Freeze: force=False returns the same IDs even if config were mutated later.
    first = registry.resolve_presets()
    cfg.models["cheap"] = ModelPreset(model="openai/gpt-4o")
    second = registry.resolve_presets()
    assert second["cheap"].model_id == first["cheap"].model_id == "openai/gpt-4o-mini"


def test_registry_default_preset_ids_match_docs() -> None:
    assert DEFAULT_PRESET_IDS["cheap"].endswith("gpt-4o-mini") or "flash" in DEFAULT_PRESET_IDS["cheap"]
    assert DEFAULT_PRESET_IDS["frontier"] in {
        "anthropic/claude-opus-4",
        "openai/gpt-4.1",
    }


def test_registry_validates_capabilities() -> None:
    cfg = AppConfig(
        models={
            "cheap": ModelPreset(model="openai/gpt-4o-mini"),
            "mid": ModelPreset(model="openai/gpt-4o"),
            "frontier": ModelPreset(model="anthropic/claude-opus-4"),
        }
    )
    bad = ModelRegistry(
        cfg,
        declare_capabilities=DeclaredCapabilities(
            tool_calling=False,
            structured_output=True,
        ),
    )
    resolved = bad.resolve_for_role(RoleName.WRITER)
    with pytest.raises(ModelCapabilityError, match="tool_calling"):
        bad.validate_capabilities(resolved)

    good = ModelRegistry(cfg)
    good.validate_all()


def test_role_agent_construction_and_tool_surfaces() -> None:
    agents = build_all_role_agents(use_test_model=True)
    assert set(agents) == set(ROLE_OUTPUT_TYPES)

    for role, agent in agents.items():
        assert agent.name == role
        assert agent.output_type is ROLE_OUTPUT_TYPES[role]
        assert_role_tool_surface(role, agent)


def test_writer_tool_surface_excludes_search_fetch_approve() -> None:
    writer = build_role_agent(RoleName.WRITER, use_test_model=True)
    names = agent_tool_names(writer)

    assert names == frozenset(WRITER_TOOLS)
    for forbidden in WRITER_FORBIDDEN_TOOL_PATTERNS:
        assert forbidden not in names
    # Broader substring guards for search/fetch/approve surfaces.
    joined = " ".join(names)
    assert "fetch" not in joined
    assert "approve_claim" not in names
    assert "search_source" not in names
    assert "execute_sql" not in names


@pytest.mark.asyncio
async def test_structured_output_validation_with_test_model() -> None:
    plan_args = ResearchPlan(project_id="p1", prompt="Explain steam engines").model_dump(
        mode="json"
    )
    agent = build_role_agent(
        RoleName.SUPERVISOR,
        model=TestModel(custom_output_args=plan_args, call_tools=[]),
    )
    deps = AgentDeps(project_id="p1", scratch={"prompt": "Explain steam engines"})
    result = await agent.run("Create a research plan.", deps=deps)
    assert isinstance(result.output, ResearchPlan)
    assert result.output.prompt == "Explain steam engines"

    # Invalid structured payload should fail during output validation / retries.
    bad_agent = build_role_agent(
        RoleName.SUPERVISOR,
        model=TestModel(custom_output_args={"not_a_plan": True}, call_tools=[]),
    )
    with pytest.raises(Exception):
        await bad_agent.run("bad", deps=deps)


@pytest.mark.asyncio
async def test_each_role_structured_output_type() -> None:
    samples: dict[str, dict] = {
        RoleName.SUPERVISOR: ResearchPlan(project_id="p", prompt="q").model_dump(mode="json"),
        RoleName.TOPIC_WORKER: ProposedClaim(statement="s").model_dump(mode="json"),
        RoleName.VERIFIER: VerificationDecision(
            claim_id="c1", decision="approve"
        ).model_dump(mode="json"),
        RoleName.COVERAGE_CRITIC: CoverageReport(plan_id="pl", plan_version=1).model_dump(
            mode="json"
        ),
        RoleName.OUTLINE_EDITOR: Outline(plan_id="pl", plan_version=1, title="T").model_dump(
            mode="json"
        ),
        RoleName.WRITER: StructuredDraft(outline_id="o1", title="T").model_dump(mode="json"),
    }
    for role, args in samples.items():
        agent = build_role_agent(
            role,
            model=TestModel(custom_output_args=args, call_tools=[]),
        )
        result = await agent.run("go", deps=AgentDeps(project_id="p"))
        assert isinstance(result.output, ROLE_OUTPUT_TYPES[role])


@pytest.mark.asyncio
async def test_run_with_usage_records_model_call(tmp_path: Path) -> None:
    path = tmp_path / "usage.deeprhetor"
    opened = await create_project_async(path, title="U", prompt="usage test")
    try:
        calls = ModelCallRepository(opened.engine)
        usage_repo = UsageRecordRepository(opened.engine)
        cfg = load_example_config()
        registry = ModelRegistry(cfg, use_test_models=True)
        resolved = registry.resolve_for_role(RoleName.WRITER)

        draft = StructuredDraft(outline_id="o1", title="Draft").model_dump(mode="json")
        agent = build_role_agent(
            RoleName.WRITER,
            model=TestModel(custom_output_args=draft, call_tools=[]),
        )
        from deeprhetor.repositories.project import ProjectRepository

        deps = AgentDeps(
            project_id=opened.project.id,
            projects=ProjectRepository(opened.engine),
            model_calls=calls,
            usage_records=usage_repo,
            scratch={"prompt": "usage test", "outline": {"title": "T", "sections": []}},
        )

        recorded = await run_with_usage(
            agent,
            "Write the report.",
            deps=deps,
            role=RoleName.WRITER,
            resolved=resolved,
            idempotency_key="writer:test:1",
        )
        assert recorded.model_call is not None
        assert recorded.model_call.role == RoleName.WRITER
        assert recorded.model_call.model_id == resolved.model_id
        assert recorded.usage_record is not None
        assert recorded.result is not None
        assert isinstance(recorded.result.output, StructuredDraft)

        again = await run_with_usage(
            agent,
            "Write the report again.",
            deps=deps,
            role=RoleName.WRITER,
            resolved=resolved,
            idempotency_key="writer:test:1",
        )
        assert again.reused_idempotent_call is True
        assert again.model_call is not None
        assert again.model_call.id == recorded.model_call.id
    finally:
        await opened.dispose()


def test_tool_names_for_role_closed_set() -> None:
    assert "search_source" in tool_names_for_role(RoleName.TOPIC_WORKER)
    assert "search_source" not in tool_names_for_role(RoleName.WRITER)
    assert "approve_claim" in tool_names_for_role(RoleName.VERIFIER)
    assert "approve_claim" not in tool_names_for_role(RoleName.WRITER)
