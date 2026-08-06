"""Wrap agent runs to persist model_call and usage_record rows."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult

from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.registry import ResolvedModel
from deeprhetor.repositories.operations import ModelCall, UsageRecord


@dataclass
class RecordedRun:
    """Agent result plus durable operation rows."""

    result: AgentRunResult[Any] | None
    model_call: ModelCall | None
    usage_record: UsageRecord | None
    latency_ms: int
    reused_idempotent_call: bool = False


def _usage_dict(usage: Any) -> dict[str, Any]:
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "requests": getattr(usage, "requests", None),
        "tool_calls": getattr(usage, "tool_calls", None),
    }


def _output_dict(output: Any) -> dict[str, Any]:
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    return {"value": str(output)}


async def run_with_usage(
    agent: Agent[AgentDeps, Any],
    user_prompt: str,
    *,
    deps: AgentDeps,
    role: str,
    resolved: ResolvedModel,
    idempotency_key: str | None = None,
    request_meta: dict[str, Any] | None = None,
) -> RecordedRun:
    """Run an agent and write ``model_call`` / ``usage_record`` when repos exist.

    If ``idempotency_key`` matches an existing model_call row, that row is
    returned without re-invoking the model.
    """
    model_calls = deps.model_calls
    usage_records = deps.usage_records

    if idempotency_key and model_calls is not None:
        existing = await model_calls.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return RecordedRun(
                result=None,
                model_call=existing,
                usage_record=None,
                latency_ms=existing.latency_ms or 0,
                reused_idempotent_call=True,
            )

    request_payload = {
        "prompt_preview": user_prompt[:500],
        "preset": resolved.preset_name,
        **(request_meta or {}),
    }

    started = time.perf_counter()
    try:
        result = await agent.run(user_prompt, deps=deps)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if model_calls is not None:
            await model_calls.create(
                provider=resolved.provider,
                model_id=resolved.model_id,
                run_id=deps.run_id,
                task_id=deps.task_id,
                role=role,
                idempotency_key=idempotency_key,
                request=request_payload,
                response={"error_type": type(exc).__name__, "message": str(exc)},
                status="error",
                latency_ms=latency_ms,
            )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = result.usage
    response = {
        "output": _output_dict(result.output),
        "usage": _usage_dict(usage),
    }

    model_call: ModelCall | None = None
    usage_record: UsageRecord | None = None
    if model_calls is not None:
        model_call = await model_calls.create(
            provider=resolved.provider,
            model_id=resolved.model_id,
            run_id=deps.run_id,
            task_id=deps.task_id,
            role=role,
            idempotency_key=idempotency_key,
            request=request_payload,
            response=response,
            status="ok",
            latency_ms=latency_ms,
        )
        if usage_records is not None:
            usage_record = await usage_records.create(
                model_call_id=model_call.id,
                run_id=deps.run_id,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )

    return RecordedRun(
        result=result,
        model_call=model_call,
        usage_record=usage_record,
        latency_ms=latency_ms,
    )


# Alias used in docs / call sites.
run_agent_recorded = run_with_usage

__all__ = ["RecordedRun", "run_agent_recorded", "run_with_usage"]
