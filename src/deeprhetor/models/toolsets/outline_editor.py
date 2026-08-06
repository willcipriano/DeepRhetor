"""Outline-editor role toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps
from deeprhetor.services.fts import FtsService


def _fts_for(deps: AgentDeps) -> FtsService | None:
    if deps.fts is not None:
        return deps.fts
    if deps.documents is not None:
        return FtsService(deps.documents.engine)
    return None


def build_outline_editor_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="outline_editor")

    @ts.tool
    async def read_approved_plan(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the approved research plan used to build the outline."""
        plan = ctx.deps.scratch.get("approved_plan") or ctx.deps.scratch.get("research_plan")
        if plan is None:
            return {"ok": False, "error": "plan_not_found"}
        return {"ok": True, "plan": plan}

    @ts.tool
    async def search_approved_claims(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search approved claims for outline placement."""
        fts = _fts_for(ctx.deps)
        if fts is not None:
            try:
                hits = await fts.search_claims(query, limit=limit)
                return {
                    "ok": True,
                    "hits": [
                        {"claim_id": h.claim_id, "statement": h.statement, "rank": h.rank}
                        for h in hits
                    ],
                }
            except Exception:
                pass
        decisions = {
            d.get("claim_id"): d.get("decision")
            for d in ctx.deps.scratch.get("verification_decisions", [])
        }
        q = query.lower()
        claims = []
        for claim in ctx.deps.scratch.get("proposed_claims", []):
            cid = claim.get("id") or claim.get("claim_id")
            if decisions and decisions.get(cid) != "approve":
                continue
            statement = str(claim.get("statement", ""))
            if q in statement.lower():
                claims.append(claim)
        return {"ok": True, "hits": claims[:limit]}

    @ts.tool
    async def read_existing_outline(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read a previously saved outline, if any."""
        outline = ctx.deps.scratch.get("outline")
        if outline is None:
            return {"ok": False, "error": "outline_not_found"}
        return {"ok": True, "outline": outline}

    @ts.tool
    async def save_outline(
        ctx: RunContext[AgentDeps],
        outline_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Save the writing outline (no web/fetch/approve tools)."""
        ctx.deps.scratch["outline"] = outline_json
        return {"ok": True, "outline": outline_json}

    return ts
