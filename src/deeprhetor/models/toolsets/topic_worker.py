"""Topic-worker role toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.toolsets._common import plugin_not_configured
from deeprhetor.services.fts import FtsService


def _fts_for(deps: AgentDeps) -> FtsService | None:
    if deps.fts is not None:
        return deps.fts
    if deps.documents is not None:
        return FtsService(deps.documents.engine)
    return None


def build_topic_worker_toolset() -> FunctionToolset[AgentDeps]:
    ts: FunctionToolset[AgentDeps] = FunctionToolset(id="topic_worker")

    @ts.tool
    async def read_assignment(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """Read the current worker assignment for this task."""
        if ctx.deps.tasks is not None and ctx.deps.task_id is not None:
            task = await ctx.deps.tasks.get(ctx.deps.task_id)
            if task is not None:
                return {
                    "ok": True,
                    "task_id": task.id,
                    "assignment": task.assignment,
                    "status": str(task.status),
                }
        assignment = ctx.deps.scratch.get("assignment")
        if assignment is not None:
            return {"ok": True, "assignment": assignment}
        return {
            "ok": False,
            "error": "assignment_not_found",
            "assignment_id": ctx.deps.assignment_id,
        }

    @ts.tool
    async def search_source(
        ctx: RunContext[AgentDeps],
        provider: str,
        query: str,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Search via a configured provider plugin (Stage 3+)."""
        plugin = ctx.deps.search_plugins.get(provider)
        if plugin is None:
            return plugin_not_configured("search", provider)
        result = await plugin.search(query, max_results=max_results)
        return {"ok": True, "provider": provider, "result": result}

    @ts.tool
    async def list_search_candidates(ctx: RunContext[AgentDeps]) -> dict[str, Any]:
        """List search hit candidates stored in scratch for this assignment."""
        hits = ctx.deps.scratch.get("search_hits", [])
        return {"ok": True, "candidates": hits}

    @ts.tool
    async def fetch_document(
        ctx: RunContext[AgentDeps],
        url: str,
        fetcher: str = "default",
    ) -> dict[str, Any]:
        """Fetch a document URL via a configured fetcher plugin (Stage 3+)."""
        plugin = ctx.deps.fetch_plugins.get(fetcher)
        if plugin is None:
            return plugin_not_configured("fetch", fetcher)
        result = await plugin.fetch(url)
        return {"ok": True, "url": url, "result": result}

    @ts.tool
    async def record_relevance(
        ctx: RunContext[AgentDeps],
        assessment_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a relevance assessment into scratch."""
        items = ctx.deps.scratch.setdefault("relevance_assessments", [])
        items.append(assessment_json)
        return {"ok": True, "assessment": assessment_json}

    @ts.tool
    async def search_archived_documents(
        ctx: RunContext[AgentDeps],
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Full-text search archived document text when FTS is available."""
        fts = _fts_for(ctx.deps)
        if fts is None:
            return {
                "ok": False,
                "error": "not_configured",
                "message": "FTS service / document repository not bound to AgentDeps.",
            }
        hits = await fts.search_documents(query, limit=limit)
        return {
            "ok": True,
            "hits": [
                {
                    "segment_id": h.segment_id,
                    "document_version_id": h.document_version_id,
                    "text": h.text,
                    "rank": h.rank,
                }
                for h in hits
            ],
        }

    @ts.tool
    async def read_document_segments(
        ctx: RunContext[AgentDeps],
        document_id: str,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read archived document segments (placeholder until segment API expands)."""
        if ctx.deps.documents is None:
            return {
                "ok": False,
                "error": "not_configured",
                "message": "Document repository not bound to AgentDeps.",
            }
        doc = await ctx.deps.documents.get(document_id)
        if doc is None:
            return {"ok": False, "error": "document_not_found", "document_id": document_id}
        return {
            "ok": True,
            "document_id": document_id,
            "title": doc.title,
            "offset": offset,
            "limit": limit,
            "segments": ctx.deps.scratch.get(f"segments:{document_id}", [])[
                offset : offset + limit
            ],
            "note": "Segment batching expands with Stage 7 scan accounting.",
        }

    @ts.tool
    async def record_segment_scan(
        ctx: RunContext[AgentDeps],
        scan_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a segment scan result (persists when ScanRepository is bound)."""
        if ctx.deps.scans is not None:
            recorded = await ctx.deps.scans.record_segment_scan(
                document_segment_id=scan_json["segment_id"],
                status=scan_json.get("status", "completed"),
                document_id=scan_json["document_id"],
                document_version_id=scan_json["document_version_id"],
                summary=scan_json.get("summary"),
                proposed_claim_ids=list(scan_json.get("proposed_claim_ids") or []),
                warnings=list(scan_json.get("warnings") or []),
                batch_index=int(scan_json.get("batch_index") or 0),
                task_id=ctx.deps.task_id,
            )
            await ctx.deps.scans.refresh_document_scan(
                document_version_id=scan_json["document_version_id"],
                document_id=scan_json["document_id"],
            )
            return {"ok": True, "scan": recorded.model_dump(mode="json")}
        scans = ctx.deps.scratch.setdefault("segment_scans", [])
        scans.append(scan_json)
        return {"ok": True, "scan": scan_json}

    @ts.tool
    async def complete_document_scan(
        ctx: RunContext[AgentDeps],
        document_id: str,
        summary_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refresh document-scan accounting; complete only when all segments are terminal."""
        if ctx.deps.scans is not None and ctx.deps.documents is not None:
            version = await ctx.deps.documents.latest_version(document_id)
            if version is None:
                return {"ok": False, "error": "document_version_not_found", "document_id": document_id}
            record = await ctx.deps.scans.refresh_document_scan(
                document_version_id=version.id,
                document_id=document_id,
            )
            return {
                "ok": True,
                "document_id": document_id,
                "is_complete": record.is_complete,
                "summary": record.summary.model_dump(mode="json") if record.summary else summary_json,
            }
        completed = ctx.deps.scratch.setdefault("completed_scans", {})
        completed[document_id] = summary_json or {"is_complete": True}
        return {"ok": True, "document_id": document_id, "summary": completed[document_id]}

    @ts.tool
    async def propose_claim(
        ctx: RunContext[AgentDeps],
        claim_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Propose a claim for later verification."""
        if ctx.deps.claims is not None:
            from deeprhetor.domain.knowledge import ProposedClaim

            claim = ProposedClaim.model_validate(claim_json)
            stored = await ctx.deps.claims.create(
                claim,
                project_id=ctx.deps.project_id,
                run_id=ctx.deps.run_id,
            )
            return {"ok": True, "claim": stored.claim.model_dump(mode="json")}
        claims = ctx.deps.scratch.setdefault("proposed_claims", [])
        claims.append(claim_json)
        return {"ok": True, "claim": claim_json}

    @ts.tool
    async def attach_evidence(
        ctx: RunContext[AgentDeps],
        claim_id: str,
        evidence_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach evidence to a proposed claim (persists when repos are bound)."""
        if ctx.deps.evidence is not None and ctx.deps.claims is not None:
            from deeprhetor.domain.enums import EvidenceDirectness, EvidenceRelation
            from deeprhetor.domain.knowledge import Evidence

            relation_raw = evidence_json.get("relation", EvidenceRelation.SUPPORTS)
            directness_raw = evidence_json.get("directness", EvidenceDirectness.DIRECT)
            explanation = str(evidence_json.get("explanation", ""))
            relation = EvidenceRelation(relation_raw)
            directness = EvidenceDirectness(directness_raw)
            payload = {
                k: v
                for k, v in evidence_json.items()
                if k not in {"relation", "directness", "explanation"}
            }
            evidence = Evidence.model_validate(payload).ensure_content_hash()
            stored = await ctx.deps.evidence.create(evidence)
            link = await ctx.deps.claims.attach_evidence(
                claim_id,
                stored.id,
                relation=relation,
                directness=directness,
                explanation=explanation,
            )
            return {
                "ok": True,
                "link": {
                    "claim_id": claim_id,
                    "evidence": stored.model_dump(mode="json"),
                    "relation": str(link.relation),
                },
            }
        links = ctx.deps.scratch.setdefault("claim_evidence", [])
        entry = {"claim_id": claim_id, "evidence": evidence_json}
        links.append(entry)
        return {"ok": True, "link": entry}

    @ts.tool
    async def record_source_note(
        ctx: RunContext[AgentDeps],
        note: str,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a free-form source note for provenance."""
        notes = ctx.deps.scratch.setdefault("source_notes", [])
        entry = {"note": note, "document_id": document_id}
        notes.append(entry)
        return {"ok": True, "entry": entry}

    return ts
