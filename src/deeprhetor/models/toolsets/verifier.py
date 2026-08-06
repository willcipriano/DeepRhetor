"""Verifier role toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps


def build_verifier_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="verifier")

    @ts.tool
    async def list_proposed_claims(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """List proposed claims awaiting verification."""
        claims = ctx.deps.scratch.get("proposed_claims", [])
        return {"ok": True, "claims": claims}

    @ts.tool
    async def read_claim_evidence(
        ctx: RunContext[AgentDeps],
        claim_id: str,
    ) -> dict[str, Any]:
        """Read evidence links attached to a claim."""
        links = [
            link
            for link in ctx.deps.scratch.get("claim_evidence", [])
            if link.get("claim_id") == claim_id
        ]
        return {"ok": True, "claim_id": claim_id, "evidence_links": links}

    @ts.tool
    async def read_exact_source_span(
        ctx: RunContext[AgentDeps],
        document_id: str,
        char_start: int,
        char_end: int,
    ) -> dict[str, Any]:
        """Read an exact archived text span for quote verification (scratch/placeholder)."""
        text_blob = ctx.deps.scratch.get(f"document_text:{document_id}", "")
        if not text_blob and ctx.deps.documents is not None:
            doc = await ctx.deps.documents.get(document_id)
            if doc is None:
                return {"ok": False, "error": "document_not_found", "document_id": document_id}
            return {
                "ok": False,
                "error": "span_store_unavailable",
                "message": (
                    "Exact segment span resolution expands with Stage 7. "
                    "Provide scratch document_text for tests."
                ),
                "document_id": document_id,
            }
        span = text_blob[char_start:char_end]
        return {
            "ok": True,
            "document_id": document_id,
            "char_start": char_start,
            "char_end": char_end,
            "text": span,
        }

    @ts.tool
    async def approve_claim(
        ctx: RunContext[AgentDeps],
        claim_id: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Approve a claim after evidence verification."""
        decisions = ctx.deps.scratch.setdefault("verification_decisions", [])
        decision = {"claim_id": claim_id, "decision": "approve", "notes": notes}
        decisions.append(decision)
        return {"ok": True, "decision": decision}

    @ts.tool
    async def reject_claim(
        ctx: RunContext[AgentDeps],
        claim_id: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Reject a claim that fails verification."""
        decisions = ctx.deps.scratch.setdefault("verification_decisions", [])
        decision = {"claim_id": claim_id, "decision": "reject", "notes": notes}
        decisions.append(decision)
        return {"ok": True, "decision": decision}

    @ts.tool
    async def request_claim_correction(
        ctx: RunContext[AgentDeps],
        claim_id: str,
        corrected_statement: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Request a corrected claim statement."""
        decisions = ctx.deps.scratch.setdefault("verification_decisions", [])
        decision = {
            "claim_id": claim_id,
            "decision": "request_correction",
            "corrected_statement": corrected_statement,
            "notes": notes,
        }
        decisions.append(decision)
        return {"ok": True, "decision": decision}

    @ts.tool
    async def record_claim_relationship(
        ctx: RunContext[AgentDeps],
        from_claim_id: str,
        to_claim_id: str,
        relation: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Record a relationship between two claims."""
        relations = ctx.deps.scratch.setdefault("claim_relations", [])
        entry = {
            "from_claim_id": from_claim_id,
            "to_claim_id": to_claim_id,
            "relation": relation,
            "notes": notes,
        }
        relations.append(entry)
        return {"ok": True, "relation": entry}

    @ts.tool
    async def find_duplicate_claims(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Heuristic duplicate detection over proposed claims in scratch."""
        claims = ctx.deps.scratch.get("proposed_claims", [])
        seen: dict[str, list[str]] = {}
        for claim in claims:
            key = str(claim.get("statement", "")).strip().lower()
            cid = str(claim.get("id", claim.get("claim_id", "")))
            seen.setdefault(key, []).append(cid)
        duplicates = [ids for ids in seen.values() if len(ids) > 1]
        return {"ok": True, "duplicate_groups": duplicates}

    return ts
