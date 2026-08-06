"""OpenRouter-backed structured draft writer with typed citations only."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.writing import CitationKey, DraftSection, Outline, StructuredDraft
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import RoleName
from deeprhetor.repositories.knowledge import ClaimRepository, StoredClaim
from deeprhetor.repositories.writing import CitationKeyRepository, DraftRepository, StoredDraft
from deeprhetor.services.writer import WriterService

logger = logging.getLogger(__name__)


class WrittenSection(BaseModel):
    section_id: str
    title: str
    prose: str = Field(
        description="Polished section prose. Do not invent facts beyond provided claims."
    )
    citation_keys: list[str] = Field(
        default_factory=list,
        description="Only citation keys supplied in the packet for this section",
    )
    claim_ids: list[str] = Field(default_factory=list)
    order: int = 0


class WrittenDraft(BaseModel):
    title: str
    abstract: str | None = None
    sections: list[WrittenSection] = Field(default_factory=list)


@dataclass
class OpenRouterWriter:
    """Frontier/mid writer that may only cite allocated citation keys."""

    config: AppConfig
    engine: Any
    claims: ClaimRepository | None = None
    citations: CitationKeyRepository | None = None
    drafts: DraftRepository | None = None
    fallback: WriterService | None = None
    last_error: str | None = None
    used_fallback: bool = False

    def __post_init__(self) -> None:
        self.claims = self.claims or ClaimRepository(self.engine)
        self.citations = self.citations or CitationKeyRepository(self.engine)
        self.drafts = self.drafts or DraftRepository(self.engine)
        self.fallback = self.fallback or WriterService(self.engine)

    async def build_and_persist(
        self,
        *,
        project_id: str,
        outline: Outline,
        abstract: str | None = None,
    ) -> tuple[StoredDraft, dict[str, CitationKey]]:
        assert self.fallback is not None
        # Allocate citation keys deterministically first.
        claim_ids = [cid for sec in outline.sections for cid in sec.claim_ids]
        claim_ids = list(dict.fromkeys(claim_ids))
        citation_map = await self.fallback.allocate_citation_keys(
            project_id=project_id, claim_ids=claim_ids
        )
        try:
            draft = await self._write_llm(
                project_id=project_id,
                outline=outline,
                citation_map=citation_map,
                abstract=abstract,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.used_fallback = True
            logger.warning("OpenRouter writer failed (%s); using deterministic writer", exc)
            return await self.fallback.build_and_persist(
                project_id=project_id, outline=outline, abstract=abstract
            )

        stored = await self.drafts.create(
            draft, project_id=project_id, outline_id=outline.id, status="structured"
        )
        return stored, citation_map

    async def _write_llm(
        self,
        *,
        project_id: str,
        outline: Outline,
        citation_map: dict[str, CitationKey],
        abstract: str | None,
    ) -> StructuredDraft:
        assert self.claims is not None
        approved = await self.claims.list_by_status(project_id, [ClaimStatus.APPROVED])
        claims_by_id = {c.id: c for c in approved}
        citations_by_claim = {c.claim_id: c for c in citation_map.values() if c.claim_id}
        allowed_keys = set(citation_map)

        packet_lines: list[str] = []
        for section in sorted(outline.sections, key=lambda s: s.order):
            packet_lines.append(f"## Section {section.section_id}: {section.title}")
            if section.notes:
                packet_lines.append(f"Notes: {section.notes}")
            if not section.claim_ids:
                packet_lines.append(
                    "(No claims — write a short framing paragraph with no citations.)"
                )
                continue
            for claim_id in section.claim_ids:
                claim = claims_by_id.get(claim_id)
                cite = citations_by_claim.get(claim_id)
                if claim is None or cite is None:
                    continue
                packet_lines.append(
                    f"- claim_id={claim_id} citation_key={cite.key}: {claim.statement}"
                )

        registry = ModelRegistry(self.config)
        # Prefer frontier for prose; fall back to mid if frontier fails at config time.
        try:
            model = registry.build_model_for_role(RoleName.WRITER)
        except Exception:
            model = registry.build_model_for_role(RoleName.OUTLINE_EDITOR)

        agent: Agent[None, WrittenDraft] = Agent(
            model,
            output_type=WrittenDraft,
            instructions=(
                "You are DeepRhetor's writer. Compose polished, non-redundant academic prose "
                "from the approved claim inventory only. Every factual statement must rest on "
                "provided claims. Put citation keys ONLY in each section's citation_keys list "
                "(never invent keys). Do not invent sources, quotations, or claims. "
                "Follow the user's rhetorical objective; do not add unsolicited counterarguments. "
                "Avoid repetition across sections. "
                "If a section has no claims, write only brief framing prose with empty "
                "citation_keys — do not invent historical facts for that section."
            ),
            name="writer",
        )
        prompt = (
            f"Report title: {outline.title}\n"
            f"Suggested abstract: {abstract or ''}\n\n"
            f"Allowed citation keys: {sorted(allowed_keys)}\n\n"
            f"Claim packets by section:\n{chr(10).join(packet_lines)}\n\n"
            "Return a WrittenDraft with one section per outlined section_id."
        )
        result = await agent.run(prompt)
        written = result.output
        if not isinstance(written, WrittenDraft):
            written = WrittenDraft.model_validate(written)

        sections: list[DraftSection] = []
        bib_keys: list[str] = []
        outline_ids = {s.section_id for s in outline.sections}
        for idx, section in enumerate(sorted(outline.sections, key=lambda s: s.order)):
            match = next((w for w in written.sections if w.section_id == section.section_id), None)
            if match is None:
                # Synthesize minimal prose from claims if the model skipped a section.
                prose, keys, cids = _fallback_section_prose(
                    section.claim_ids, claims_by_id, citations_by_claim
                )
                sections.append(
                    DraftSection(
                        section_id=section.section_id,
                        title=section.title,
                        prose=prose,
                        citation_keys=keys,
                        claim_ids=cids,
                        order=section.order,
                    )
                )
                for k in keys:
                    if k not in bib_keys:
                        bib_keys.append(k)
                continue

            if not section.claim_ids:
                # Hard-disable invention: only short framing when no evidence packet.
                prose = (
                    match.prose.strip()
                    if match and match.prose and len(match.prose.strip()) < 280
                    else (
                        section.notes
                        or f"This section frames {section.title.lower()} for the research objective."
                    )
                )
                sections.append(
                    DraftSection(
                        section_id=section.section_id,
                        title=section.title,
                        prose=prose,
                        citation_keys=[],
                        claim_ids=[],
                        order=section.order if section.order else idx,
                    )
                )
                continue

            keys = [k for k in match.citation_keys if k in allowed_keys]
            # Ensure claims assigned to the section remain attached when cited.
            claim_ids = [
                cid
                for cid in section.claim_ids
                if cid in claims_by_id
                and citations_by_claim.get(cid)
                and citations_by_claim[cid].key in keys
            ]
            if not claim_ids:
                claim_ids = list(section.claim_ids)
            prose = (match.prose or "").strip()
            if not prose:
                prose, keys, claim_ids = _fallback_section_prose(
                    section.claim_ids, claims_by_id, citations_by_claim
                )
            sections.append(
                DraftSection(
                    section_id=section.section_id,
                    title=match.title or section.title,
                    prose=prose,
                    citation_keys=keys,
                    claim_ids=claim_ids,
                    order=section.order if section.order else idx,
                )
            )
            for k in keys:
                if k not in bib_keys:
                    bib_keys.append(k)

        # Drop unexpected sections from the model.
        _ = outline_ids
        return StructuredDraft(
            outline_id=outline.id,
            title=written.title or outline.title,
            abstract=written.abstract or abstract,
            sections=sections,
            bibliography_keys=bib_keys,
        )


def _fallback_section_prose(
    claim_ids: list[str],
    claims_by_id: dict[str, StoredClaim],
    citations_by_claim: dict[str, CitationKey],
) -> tuple[str, list[str], list[str]]:
    parts: list[str] = []
    keys: list[str] = []
    kept: list[str] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        cite = citations_by_claim.get(claim_id)
        if claim is None or cite is None:
            continue
        parts.append(claim.statement.rstrip(".") + ".")
        keys.append(cite.key)
        kept.append(claim_id)
    if not parts:
        return "This section frames the research objective.", [], []
    return " ".join(parts), keys, kept
