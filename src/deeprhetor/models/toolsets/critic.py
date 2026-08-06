"""Coverage-critic role toolset — gap requests only; never dispatches workers."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.planning import CoverageGapRequest, ResearchPlan
from deeprhetor.models.deps import AgentDeps
from deeprhetor.services.critic import CriticLoopState


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
        if ctx.deps.critic is not None and ctx.deps.claims is not None:
            plan_raw = ctx.deps.scratch.get("approved_plan") or ctx.deps.scratch.get("research_plan")
            if plan_raw is None:
                return {"ok": False, "error": "plan_not_found"}
            plan = ResearchPlan.model_validate(plan_raw) if isinstance(plan_raw, dict) else plan_raw
            state = ctx.deps.scratch.get("critic_loop_state") or CriticLoopState()
            result = await ctx.deps.critic.evaluate(
                plan=plan,
                project_id=ctx.deps.project_id,
                plan_id=getattr(plan, "id", None),
                run_id=ctx.deps.run_id,
                state=state if isinstance(state, CriticLoopState) else CriticLoopState(),
            )
            ctx.deps.scratch["critic_loop_state"] = result.state
            ctx.deps.scratch["coverage_report"] = result.report.model_dump(mode="json")
            return {
                "ok": True,
                "report": result.report.model_dump(mode="json"),
                "should_continue": result.should_continue,
                "terminated_reason": result.state.terminated_reason,
            }

        plan = ctx.deps.scratch.get("approved_plan") or ctx.deps.scratch.get("research_plan") or {}
        if ctx.deps.claims is not None:
            proposed = await ctx.deps.claims.list_for_project(
                ctx.deps.project_id, status=ClaimStatus.PROPOSED
            )
            approved = await ctx.deps.claims.list_for_project(
                ctx.deps.project_id, status=ClaimStatus.APPROVED
            )
            return {
                "ok": True,
                "section_count": len(plan.get("sections", []) if isinstance(plan, dict) else []),
                "proposed_claim_count": len(proposed),
                "approved_claim_count": len(approved),
            }
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
        if ctx.deps.claims is not None and ctx.deps.evidence is not None:
            rows = await ctx.deps.claims.list_for_project(ctx.deps.project_id)
            unsupported: list[str] = []
            for row in rows:
                pairs = await ctx.deps.evidence.list_for_claim(row.id)
                if not pairs:
                    unsupported.append(row.id)
            return {"ok": True, "unsupported_claim_ids": unsupported}
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
        """List documents not marked complete in scan accounting."""
        if ctx.deps.scans is not None:
            incomplete = await ctx.deps.scans.list_incomplete_document_scans()
            return {
                "ok": True,
                "unscanned_document_ids": [s.document_version_id for s in incomplete],
            }
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
        """Request focused gap research (supervisor dispatches workers — critic never does)."""
        gap = CoverageGapRequest.model_validate(gap_json)
        gaps = ctx.deps.scratch.setdefault("gap_requests", [])
        payload = gap.model_dump(mode="json")
        gaps.append(payload)
        return {"ok": True, "gap": payload}

    @ts.tool
    async def mark_research_complete(
        ctx: RunContext[AgentDeps],
        notes: str = "",
    ) -> dict[str, Any]:
        """Signal that coverage criteria are met."""
        ctx.deps.scratch["research_complete"] = {"notes": notes}
        return {"ok": True, "complete": True, "notes": notes}

    return ts
