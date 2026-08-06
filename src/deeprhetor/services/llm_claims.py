"""OpenRouter-backed claim proposal from archived segments."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.knowledge import (
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from deeprhetor.domain.planning import ResearchPlan
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import RoleName
from deeprhetor.repositories.document import DocumentRepository, DocumentSegment
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository

logger = logging.getLogger(__name__)


class ExtractedEvidence(BaseModel):
    quote: str = Field(description="Verbatim quote copied from the provided source text")
    document_id: str
    document_version_id: str
    document_segment_id: str | None = None


class ExtractedClaim(BaseModel):
    statement: str = Field(
        description="Atomic claim that supports the research objective using the quote"
    )
    topic_id: str
    evidence: ExtractedEvidence
    notes: str | None = None


class ClaimExtractionBatch(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


@dataclass
class OpenRouterClaimProposer:
    """Propose grounded claims with an LLM; reject any non-verbatim quotes."""

    config: AppConfig
    max_claims: int = 10
    max_segments_per_doc: int = 4
    max_docs_per_topic: int = 3
    max_segment_chars: int = 1200
    proposed_ids: list[str] = field(default_factory=list)
    last_error: str | None = None
    used_fallback: bool = False
    fallback: Any | None = None

    async def propose_for_plan(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: ResearchPlan,
        documents: DocumentRepository,
        claims: ClaimRepository,
        evidence: EvidenceRepository,
    ) -> list[str]:
        try:
            return await self._propose_llm(
                project_id=project_id,
                run_id=run_id,
                plan=plan,
                documents=documents,
                claims=claims,
                evidence=evidence,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.used_fallback = True
            logger.warning("OpenRouter claim extraction failed (%s); using fallback", exc)
            if self.fallback is None:
                raise
            return await self.fallback.propose_for_plan(
                project_id=project_id,
                run_id=run_id,
                plan=plan,
                documents=documents,
                claims=claims,
                evidence=evidence,
            )

    async def _propose_llm(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: ResearchPlan,
        documents: DocumentRepository,
        claims: ClaimRepository,
        evidence: EvidenceRepository,
    ) -> list[str]:
        registry = ModelRegistry(self.config)
        model = registry.build_model_for_role(RoleName.TOPIC_WORKER)
        agent: Agent[None, ClaimExtractionBatch] = Agent(
            model,
            output_type=ClaimExtractionBatch,
            instructions=(
                "You extract atomic research claims from archived source excerpts. "
                "Every claim must support the user's research objective. "
                "Every evidence.quote MUST be copied verbatim from the provided text "
                "(substring match). Do not invent facts or quotes. Prefer 1–2 strong "
                "claims per topic. If evidence is weak, output fewer claims."
            ),
            name="claim_extractor",
        )

        docs = await documents.list_for_project(project_id)
        packet = await self._build_packet(plan, documents, docs)
        if not packet["segments"]:
            raise RuntimeError("no archived segments available for claim extraction")

        prompt = (
            f"Research objective:\n{plan.prompt}\n\n"
            f"Topics:\n{packet['topics']}\n\n"
            f"Source excerpts (use only these texts for quotes):\n{packet['segments']}\n\n"
            f"Return at most {self.max_claims} claims."
        )
        result = await agent.run(prompt)
        batch = result.output
        if not isinstance(batch, ClaimExtractionBatch):
            batch = ClaimExtractionBatch.model_validate(batch)

        claim_ids: list[str] = []
        for item in batch.claims:
            if len(claim_ids) >= self.max_claims:
                break
            seg_text = packet["segment_text"].get(item.evidence.document_segment_id or "")
            if not seg_text:
                # Try any segment for this version.
                seg_text = packet["version_text"].get(item.evidence.document_version_id, "")
            quote = (item.evidence.quote or "").strip()
            if not quote or quote not in seg_text:
                logger.info("reject claim with non-verbatim quote: %s", quote[:80])
                continue
            seg_id = item.evidence.document_segment_id
            start = seg_text.find(quote)
            claim = ProposedClaim(
                statement=item.statement.strip(),
                topic_id=item.topic_id or None,
                project_id=project_id,
                run_id=run_id,
                worker_notes=item.notes,
            )
            stored = await claims.create(claim, project_id=project_id, run_id=run_id)
            ev = Evidence(
                document_id=item.evidence.document_id,
                document_version_id=item.evidence.document_version_id,
                document_segment_id=seg_id,
                quote=quote,
                location=EvidenceLocation(
                    char_start=start if start >= 0 else 0,
                    char_end=(start if start >= 0 else 0) + len(quote),
                ),
                content_hash=quote_content_hash(quote),
            )
            created = await evidence.create(ev)
            await claims.attach_evidence(stored.id, created.id)
            claim_ids.append(stored.id)

        if not claim_ids:
            raise RuntimeError("LLM produced no claims with verifying verbatim quotes")
        self.proposed_ids.extend(claim_ids)
        return claim_ids

    async def _build_packet(
        self,
        plan: ResearchPlan,
        documents: DocumentRepository,
        docs: list[Any],
    ) -> dict[str, Any]:
        topics_blob = "\n".join(
            f"- {t.topic_id}: {t.title} — {t.objective}" for t in plan.topics
        )
        segment_lines: list[str] = []
        segment_text: dict[str, str] = {}
        version_text: dict[str, str] = {}

        for topic in plan.topics:
            topic_docs = [
                d
                for d in docs
                if isinstance(getattr(d, "metadata", None), dict)
                and d.metadata.get("topic_id") == topic.topic_id
            ] or list(docs)
            for doc in topic_docs[: self.max_docs_per_topic]:
                version = await documents.latest_version(doc.id)
                if version is None:
                    continue
                segs = await documents.list_segments(version.id)
                chosen = _pick_informative_segments(
                    segs,
                    keywords=_tokens(plan.prompt + " " + topic.objective + " " + topic.title),
                    limit=self.max_segments_per_doc,
                    max_chars=self.max_segment_chars,
                )
                joined = "\n\n".join(s.text for s in segs)
                version_text[version.id] = joined
                for seg in chosen:
                    clipped = seg.text[: self.max_segment_chars]
                    segment_text[seg.id] = seg.text
                    segment_lines.append(
                        f"[doc_id={doc.id} version_id={version.id} segment_id={seg.id} "
                        f"title={doc.title!r} topic_id={topic.topic_id}]\n{clipped}"
                    )

        return {
            "topics": topics_blob,
            "segments": "\n\n---\n\n".join(segment_lines[:40]),
            "segment_text": segment_text,
            "version_text": version_text,
        }


def _tokens(text: str) -> list[str]:
    import re

    seen: list[str] = []
    for w in re.findall(r"[A-Za-z]{4,}", text.lower()):
        if w not in seen:
            seen.append(w)
    return seen[:20]


def _pick_informative_segments(
    segments: list[DocumentSegment],
    *,
    keywords: list[str],
    limit: int,
    max_chars: int,
) -> list[DocumentSegment]:
    scored: list[tuple[int, DocumentSegment]] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if len(text) < 40:
            continue
        if text.count("|") >= 3 or "---|---" in text:
            continue
        lower = text.lower()
        score = sum(1 for kw in keywords if kw in lower)
        scored.append((score, seg))
    scored.sort(key=lambda item: (-item[0], item[1].segment_index))
    out: list[DocumentSegment] = []
    for _, seg in scored[:limit]:
        out.append(seg)
    if not out and segments:
        out = segments[:limit]
    return out
