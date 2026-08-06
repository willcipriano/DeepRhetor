"""Coverage-critic role toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps


def build_critic_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="coverage_critic")

    @ts.tool
    async def read_approved_plan(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the approved research plan from scratch."""
        plan = ctx.deps.scratch.get("approved_plan") or ctx.deps.scratch.get("research_plan")
        if plan is None:
            return {"ok": False, "error": "plan_not_found"}
        return {"ok": True, "plan": plan}

    @ts.tool
    async def inspect_claim_coverage(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Summarize claim counts relative to plan sections/topics."""
        plan = ctx.deps.scratch.get("approved_plan") or ctx.deps.scratch.get("research_plan") or {}
        claims = ctx.deps.scratch.get("proposed_claims", [])
        decisions = {
            d.get("claim_id"): d.get("decision")
            for d in ctx.deps.scratch.get("verification_decisions", [])
        }
        approved = [
            c
            for c in claims
            if decisions.get(c.get("id"), decisions.get(c.get("claim_id"))) == "approve"
        ]
        sections = plan.get("sections", []) if isinstance(plan, dict) else []
        return {
            "ok": True,
            "section_count": len(sections),
            "proposed_claim_count": len(claims),
            "approved_claim_count": len(approved),
        }

    @ts.tool
    async def find_unsupported_claims(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Find claims lacking evidence links."""
        claims = ctx.deps.scratch.get("proposed_claims", [])
        linked = {
            link.get("claim_id") for link in ctx.deps.scratch.get("claim_evidence", [])
        }
        unsupported = [
            c.get("id") or c.get("claim_id")
            for c in claims
            if (c.get("id") or c.get("claim_id")) not in linked
            and not c.get("evidence_links")
        ]
        return {"ok": True, "unsupported_claim_ids": unsupported}

    @ts.tool
    async def find_unscanned_documents(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """List documents not marked complete in scratch scans."""
        known = set(ctx.deps.scratch.get("known_document_ids", []))
        completed = set(ctx.deps.scratch.get("completed_scans", {}).keys())
        return {"ok": True, "unscanned_document_ids": sorted(known - completed)}

    @ts.tool
    async def inspect_source_diversity(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Summarize source-class diversity from scratch metadata."""
        classes = ctx.deps.scratch.get("source_classes", {})
        return {"ok": True, "source_classes": classes}

    @ts.tool
    async def record_coverage_report(
        ctx: RunContext[AgentDeps],
        report_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a coverage report into scratch for the supervisor."""
        ctx.deps.scratch["coverage_report"] = report_json
        return {"ok": True, "report": report_json}

    @ts.tool
    async def request_research_gap(
        ctx: RunContext[AgentDeps],
        gap_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Request focused gap research (supervisor dispatches workers)."""
        gaps = ctx.deps.scratch.setdefault("gap_requests", [])
        gaps.append(gap_json)
        return {"ok": True, "gap": gap_json}

    @ts.tool
    async def mark_research_complete(
        ctx: RunContext[AgentDeps],
        notes: str = "",
    ) -> dict[str, Any]:
        """Signal that coverage criteria are met."""
        ctx.deps.scratch["research_complete"] = {"notes": notes}
        return {"ok": True, "complete": True, "notes": notes}

    return ts
