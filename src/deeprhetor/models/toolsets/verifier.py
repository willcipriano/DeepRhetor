"""Verifier role toolset — deterministic quote checks against archives."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.knowledge import ClaimRelation
from deeprhetor.models.deps import AgentDeps
from deeprhetor.services.verify import VerifierService


def _verifier(deps: AgentDeps) -> VerifierService | None:
    if deps.verifier is not None:
        return deps.verifier
    if deps.claims is not None and deps.evidence is not None:
        return VerifierService(
            deps.claims.engine,
            claims=deps.claims,
            evidence=deps.evidence,
            documents=deps.documents,
        )
    return None


def build_verifier_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="verifier")

    @ts.tool
    async def list_proposed_claims(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """List proposed claims awaiting verification."""
        if ctx.deps.claims is not None:
            rows = await ctx.deps.claims.list_for_project(
                ctx.deps.project_id, status=ClaimStatus.PROPOSED, run_id=ctx.deps.run_id
            )
            return {
                "ok": True,
                "claims": [
                    {
                        "id": r.id,
                        "statement": r.statement,
                        "status": str(r.status),
                        "topic_id": r.topic_id,
                    }
                    for r in rows
                ],
            }
        claims = ctx.deps.scratch.get("proposed_claims", [])
        return {"ok": True, "claims": claims}

    @ts.tool
    async def read_claim_evidence(
        ctx: RunContext[AgentDeps],
        claim_id: str,
    ) -> dict[str, Any]:
        """Read evidence links attached to a claim."""
        if ctx.deps.evidence is not None:
            pairs = await ctx.deps.evidence.list_for_claim(claim_id)
            return {
                "ok": True,
                "claim_id": claim_id,
                "evidence_links": [
                    {
                        "evidence": evidence.model_dump(mode="json"),
                        "relation": str(link.relation),
                        "directness": str(link.directness),
                        "explanation": link.explanation,
                    }
                    for evidence, link in pairs
                ],
            }
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
        document_version_id: str | None = None,
    ) -> dict[str, Any]:
        """Read an exact archived text span for quote verification."""
        verifier = _verifier(ctx.deps)
        if verifier is not None and document_version_id:
            text_blob = await verifier.get_normalized_text(document_version_id)
            span = text_blob[char_start:char_end]
            return {
                "ok": True,
                "document_id": document_id,
                "document_version_id": document_version_id,
                "char_start": char_start,
                "char_end": char_end,
                "text": span,
            }
        text_blob = ctx.deps.scratch.get(f"document_text:{document_id}", "")
        if not text_blob:
            return {
                "ok": False,
                "error": "span_store_unavailable",
                "message": "Provide document_version_id with VerifierService or scratch text.",
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
        """Approve a claim after deterministic evidence verification."""
        verifier = _verifier(ctx.deps)
        if verifier is not None:
            decision = await verifier.approve_claim(claim_id, notes=notes)
            return {"ok": decision.decision.value == "approve", "decision": decision.model_dump(mode="json")}
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
        verifier = _verifier(ctx.deps)
        if verifier is not None:
            decision = await verifier.reject_claim(claim_id, notes=notes)
            return {"ok": True, "decision": decision.model_dump(mode="json")}
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
        if ctx.deps.claims is not None:
            stored = await ctx.deps.claims.transition(
                claim_id,
                ClaimStatus.NEEDS_CORRECTION,
                notes=notes,
                corrected_statement=corrected_statement,
            )
            return {
                "ok": True,
                "decision": {
                    "claim_id": claim_id,
                    "decision": "request_correction",
                    "corrected_statement": corrected_statement,
                    "notes": notes,
                    "status": str(stored.status),
                },
            }
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
        entry = ClaimRelation(
            from_claim_id=from_claim_id,
            to_claim_id=to_claim_id,
            relation=relation,
            notes=notes or None,
        )
        if ctx.deps.claims is not None:
            await ctx.deps.claims.record_relation(entry)
            return {"ok": True, "relation": entry.model_dump(mode="json")}
        relations = ctx.deps.scratch.setdefault("claim_relations", [])
        payload = entry.model_dump(mode="json")
        relations.append(payload)
        return {"ok": True, "relation": payload}

    @ts.tool
    async def find_duplicate_claims(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Heuristic duplicate detection over proposed/approved claims."""
        if ctx.deps.claims is not None:
            rows = await ctx.deps.claims.list_for_project(ctx.deps.project_id)
            claims = [{"id": r.id, "statement": r.statement} for r in rows]
        else:
            claims = ctx.deps.scratch.get("proposed_claims", [])
        seen: dict[str, list[str]] = {}
        for claim in claims:
            key = str(claim.get("statement", "")).strip().lower()
            cid = str(claim.get("id", claim.get("claim_id", "")))
            seen.setdefault(key, []).append(cid)
        duplicates = [ids for ids in seen.values() if len(ids) > 1]
        return {"ok": True, "duplicate_groups": duplicates}

    return ts
