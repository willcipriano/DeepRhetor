"""OpenRouter frontier writer: Markdown drafts with typed citation markers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.writing import CitationKey, MarkdownDraft, Outline
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import RoleName
from deeprhetor.repositories.knowledge import ClaimRepository, StoredClaim
from deeprhetor.repositories.writing import CitationKeyRepository, DraftRepository, StoredDraft
from deeprhetor.services.citation_validate import extract_markdown_citation_keys
from deeprhetor.services.writer import WriterService

logger = logging.getLogger(__name__)


class WrittenMarkdown(BaseModel):
    title: str
    abstract: str | None = None
    markdown: str = Field(
        description=(
            "Full scholarly Markdown document with # headings. "
            "Cite only with [@cite_key] markers. Do not emit LaTeX."
        )
    )
    bibliography_keys: list[str] = Field(default_factory=list)


@dataclass
class OpenRouterWriter:
    """Frontier writer that drafts in Markdown (typesetting is a later phase)."""

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
    ) -> tuple[StoredDraft, dict[str, CitationKey], MarkdownDraft]:
        assert self.fallback is not None
        claim_ids = [cid for sec in outline.sections for cid in sec.claim_ids]
        claim_ids = list(dict.fromkeys(claim_ids))
        citation_map = await self.fallback.allocate_citation_keys(
            project_id=project_id, claim_ids=claim_ids
        )
        try:
            md = await self._write_llm(
                project_id=project_id,
                outline=outline,
                citation_map=citation_map,
                abstract=abstract,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.used_fallback = True
            logger.warning("OpenRouter markdown writer failed (%s); using deterministic MD", exc)
            approved = await self.claims.list_by_status(project_id, [ClaimStatus.APPROVED])
            md = self.fallback.build_markdown(
                outline=outline,
                project_id=project_id,
                citation_map=citation_map,
                abstract=abstract,
                claims_by_id={c.id: c for c in approved},
            )

        stored = await self.drafts.create(
            md, project_id=project_id, outline_id=outline.id, status="markdown"
        )
        persisted = stored.markdown_draft or md.model_copy(update={"id": stored.id})
        return stored, citation_map, persisted

    async def _write_llm(
        self,
        *,
        project_id: str,
        outline: Outline,
        citation_map: dict[str, CitationKey],
        abstract: str | None,
    ) -> MarkdownDraft:
        assert self.claims is not None
        approved = await self.claims.list_by_status(project_id, [ClaimStatus.APPROVED])
        claims_by_id = {c.id: c for c in approved}
        citations_by_claim = {c.claim_id: c for c in citation_map.values() if c.claim_id}
        allowed_keys = set(citation_map)

        packet_lines: list[str] = []
        for section in sorted(outline.sections, key=lambda s: s.order):
            packet_lines.append(f"## Outline section `{section.section_id}`: {section.title}")
            if section.notes:
                packet_lines.append(f"Notes: {section.notes}")
            if not section.claim_ids:
                packet_lines.append(
                    "(No claims — short framing only; no citation markers.)"
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
        try:
            model = registry.build_model_for_role(RoleName.WRITER)
        except Exception:
            model = registry.build_model_for_role(RoleName.OUTLINE_EDITOR)

        agent: Agent[None, WrittenMarkdown] = Agent(
            model,
            output_type=WrittenMarkdown,
            instructions=(
                "You are DeepRhetor's frontier writer. Draft a polished scholarly report in "
                "Markdown only — never LaTeX, never backslash TeX commands. "
                "Use ## section headings (include Introduction and Conclusion) and "
                "normal paragraphs. "
                "Cite approved claims only with pandoc-style markers like [@cite_001] "
                "placed inline after the supported sentence. "
                "Do not invent sources, quotations, or citation keys. "
                "Follow the user's rhetorical objective; do not add unsolicited counterarguments. "
                "If a section has no claims, write brief framing only with no citations."
            ),
            name="markdown_writer",
        )
        prompt = (
            f"Report title: {outline.title}\n"
            f"Suggested abstract: {abstract or ''}\n\n"
            f"Allowed citation keys: {sorted(allowed_keys)}\n\n"
            f"Claim packets by outline section:\n{chr(10).join(packet_lines)}\n\n"
            "Return WrittenMarkdown. bibliography_keys must be a subset of allowed keys."
        )
        result = await agent.run(prompt)
        written = result.output
        if not isinstance(written, WrittenMarkdown):
            written = WrittenMarkdown.model_validate(written)

        markdown = (written.markdown or "").strip()
        if not markdown:
            raise RuntimeError("writer returned empty markdown")
        if "\\documentclass" in markdown or "\\begin{document}" in markdown:
            raise RuntimeError("writer emitted LaTeX; markdown-only required")

        used = extract_markdown_citation_keys(markdown)
        bib_keys = [k for k in (written.bibliography_keys or sorted(used)) if k in allowed_keys]
        for k in sorted(used):
            if k not in bib_keys and k in allowed_keys:
                bib_keys.append(k)
        unknown = used - allowed_keys
        if unknown:
            # Strip unknown markers rather than failing hard.
            for key in unknown:
                markdown = markdown.replace(f"[@{key}]", "").replace(f"[^{key}]", "")
            logger.info("stripped unknown citation keys from markdown: %s", sorted(unknown))

        claim_ids = [
            cid
            for cid in claim_ids_from_outline(outline)
            if cid in claims_by_id
            and citations_by_claim.get(cid)
            and citations_by_claim[cid].key in set(bib_keys) | used
        ]
        if not claim_ids:
            claim_ids = claim_ids_from_outline(outline)

        return MarkdownDraft(
            outline_id=outline.id,
            title=written.title or outline.title,
            abstract=written.abstract or abstract,
            markdown=markdown,
            bibliography_keys=bib_keys,
            claim_ids=claim_ids,
        )


def claim_ids_from_outline(outline: Outline) -> list[str]:
    ids: list[str] = []
    for section in outline.sections:
        for cid in section.claim_ids:
            if cid not in ids:
                ids.append(cid)
    return ids
