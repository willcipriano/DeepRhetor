"""Writer role toolset — no web-search, fetch, or claim-approval tools."""

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


def build_writer_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="writer")

    @ts.tool
    async def read_authoritative_prompt(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the user's authoritative project prompt."""
        if ctx.deps.projects is not None:
            project = await ctx.deps.projects.get(ctx.deps.project_id)
            if project is not None:
                return {
                    "ok": True,
                    "project_id": project.id,
                    "title": project.title,
                    "prompt": project.prompt,
                }
        prompt = ctx.deps.scratch.get("prompt")
        if prompt is not None:
            return {"ok": True, "prompt": prompt, "source": "scratch"}
        return {"ok": False, "error": "project_not_found"}


    @ts.tool
    async def read_approved_outline(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the approved writing outline."""
        outline = ctx.deps.scratch.get("outline") or ctx.deps.scratch.get("approved_outline")
        if outline is None:
            return {"ok": False, "error": "outline_not_found"}
        return {"ok": True, "outline": outline}

    @ts.tool
    async def search_approved_claims(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the approved claim inventory (not the open web)."""
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
        q = query.lower()
        claims = [
            c
            for c in ctx.deps.scratch.get("approved_claims", ctx.deps.scratch.get("proposed_claims", []))
            if q in str(c.get("statement", "")).lower()
        ]
        return {"ok": True, "hits": claims[:limit]}

    @ts.tool
    async def get_section_claim_packet(
        ctx: RunContext[AgentDeps],
        section_id: str,
    ) -> dict[str, Any]:
        """Return the claim packet assigned to an outline section."""
        outline = ctx.deps.scratch.get("outline") or ctx.deps.scratch.get("approved_outline") or {}
        claim_ids: list[str] = []
        for section in outline.get("sections", []) if isinstance(outline, dict) else []:
            if section.get("section_id") == section_id:
                claim_ids = list(section.get("claim_ids", []))
                break
        claims_by_id = {
            (c.get("id") or c.get("claim_id")): c
            for c in ctx.deps.scratch.get(
                "approved_claims", ctx.deps.scratch.get("proposed_claims", [])
            )
        }
        packet = [claims_by_id[cid] for cid in claim_ids if cid in claims_by_id]
        return {"ok": True, "section_id": section_id, "claim_ids": claim_ids, "claims": packet}

    @ts.tool
    async def resolve_citation_key(
        ctx: RunContext[AgentDeps],
        claim_id: str,
    ) -> dict[str, Any]:
        """Resolve or allocate a bibliography citation key for a claim."""
        keys = ctx.deps.scratch.setdefault("citation_keys", {})
        if claim_id in keys:
            return {"ok": True, "claim_id": claim_id, "citation_key": keys[claim_id]}
        key = f"cite_{len(keys) + 1:03d}"
        keys[claim_id] = key
        return {"ok": True, "claim_id": claim_id, "citation_key": key}

    @ts.tool
    async def read_existing_draft_sections(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read draft sections already saved for this writing pass."""
        sections = ctx.deps.scratch.get("draft_sections", [])
        return {"ok": True, "sections": sections}

    @ts.tool
    async def save_draft_section(
        ctx: RunContext[AgentDeps],
        section_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Save a structured draft section."""
        sections = ctx.deps.scratch.setdefault("draft_sections", [])
        sid = section_json.get("section_id")
        if sid is not None:
            sections[:] = [s for s in sections if s.get("section_id") != sid]
        sections.append(section_json)
        return {"ok": True, "section": section_json}

    return ts
