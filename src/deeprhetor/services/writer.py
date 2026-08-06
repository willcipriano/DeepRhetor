"""Deterministic structured-draft builder from outline + approved claims."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ClaimStatus
from deeprhetor.domain.writing import (
    BibEntry,
    CitationKey,
    DraftSection,
    MarkdownDraft,
    Outline,
    StructuredDraft,
)
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository, StoredClaim
from deeprhetor.repositories.writing import (
    CitationKeyRepository,
    DraftRepository,
    StoredDraft,
)


class WriterService:
    """Build a structured draft with typed citation IDs (no frontier model required)."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        claims: ClaimRepository | None = None,
        evidence: EvidenceRepository | None = None,
        documents: DocumentRepository | None = None,
        citations: CitationKeyRepository | None = None,
        drafts: DraftRepository | None = None,
    ) -> None:
        self._engine = engine
        self.claims = claims or ClaimRepository(engine)
        self.evidence = evidence or EvidenceRepository(engine)
        self.documents = documents or DocumentRepository(engine)
        self.citations = citations or CitationKeyRepository(engine)
        self.drafts = drafts or DraftRepository(engine)

    async def allocate_citation_keys(
        self,
        *,
        project_id: str,
        claim_ids: list[str],
    ) -> dict[str, CitationKey]:
        """Ensure each claim has a stable citation key + bib metadata.

        Returns a map keyed by citation key (e.g. ``cite_001``).
        """
        by_key: dict[str, CitationKey] = {}
        by_claim: dict[str, CitationKey] = {}
        existing = await self.citations.list_for_project(project_id)
        for cite in existing:
            by_key[cite.key] = cite
            if cite.claim_id:
                by_claim[cite.claim_id] = cite

        for claim_id in claim_ids:
            if claim_id in by_claim:
                continue
            claim = await self.claims.get(claim_id)
            if claim is None:
                continue
            key = f"cite_{len(by_key) + 1:03d}"
            evidence_rows = await self.evidence.list_for_claim(claim_id)
            evidence_id = None
            document_id = None
            document_version_id = None
            content_sha = None
            bib = BibEntry(
                key=key,
                entry_type="misc",
                title=claim.statement[:200],
                author="Archived Source",
                year="",
                note=f"Claim {claim_id}",
            )
            if evidence_rows:
                ev, _link = evidence_rows[0]
                evidence_id = ev.id
                document_id = ev.document_id
                document_version_id = ev.document_version_id
                version = await self.documents.get_version(ev.document_version_id)
                if version is not None:
                    content_sha = version.content_sha256
                doc = await self.documents.get(ev.document_id)
                if doc is not None:
                    entry_type = "online" if doc.canonical_url else "misc"
                    bib = BibEntry(
                        key=key,
                        entry_type=entry_type,
                        title=doc.title or claim.statement[:200],
                        author="Archived Source",
                        year="",
                        url=doc.canonical_url,
                        howpublished="Online source" if doc.canonical_url else None,
                        note=f"Document {doc.id}",
                    )
            citation = CitationKey(
                project_id=project_id,
                key=key,
                claim_id=claim_id,
                evidence_id=evidence_id,
                document_id=document_id,
                document_version_id=document_version_id,
                document_content_sha256=content_sha,
                bib=bib,
            )
            saved = await self.citations.upsert(citation)
            by_key[saved.key] = saved
            by_claim[claim_id] = saved
        return by_key

    def build_draft(
        self,
        outline: Outline,
        claims_by_id: dict[str, StoredClaim],
        citations_by_key: dict[str, CitationKey],
        *,
        abstract: str | None = None,
    ) -> StructuredDraft:
        """Deterministic prose from claim statements; citations are typed keys only."""
        citations_by_claim = {
            c.claim_id: c for c in citations_by_key.values() if c.claim_id
        }
        sections: list[DraftSection] = []
        bib_keys: list[str] = []
        for section in sorted(outline.sections, key=lambda s: s.order):
            prose_parts: list[str] = []
            cited_keys: list[str] = []
            claim_ids: list[str] = []
            for claim_id in section.claim_ids:
                claim = claims_by_id.get(claim_id)
                if claim is None or claim.status != ClaimStatus.APPROVED:
                    continue
                citation = citations_by_claim.get(claim_id)
                if citation is None:
                    continue
                prose_parts.append(claim.statement.rstrip(".") + ".")
                cited_keys.append(citation.key)
                claim_ids.append(claim_id)
                if citation.key not in bib_keys:
                    bib_keys.append(citation.key)
            if not prose_parts:
                if section.section_id.endswith("introduction") or section.title.lower() == "introduction":
                    prose = (
                        f"This report investigates the following research objective: "
                        f"{outline.title}."
                    )
                elif section.section_id.endswith("conclusion") or section.title.lower() == "conclusion":
                    prose = (
                        "Taken together, the archived evidence above is offered in support "
                        "of the research objective without inventing additional sources."
                    )
                else:
                    prose = section.notes or f"Section {section.title}."
            else:
                # Join evidence-bearing claims into continuous prose.
                prose = " ".join(prose_parts)
            sections.append(
                DraftSection(
                    section_id=section.section_id,
                    title=section.title,
                    prose=prose,
                    citation_keys=cited_keys,
                    claim_ids=claim_ids,
                    order=section.order,
                )
            )
        return StructuredDraft(
            outline_id=outline.id,
            title=outline.title,
            abstract=abstract,
            sections=sections,
            bibliography_keys=bib_keys,
        )

    def build_markdown(
        self,
        *,
        outline: Outline,
        project_id: str,
        citation_map: dict[str, CitationKey],
        abstract: str | None = None,
        claims_by_id: dict[str, StoredClaim] | None = None,
    ) -> MarkdownDraft:
        """Deterministic Markdown draft (fallback when frontier writer unavailable)."""
        citations_by_claim = {
            c.claim_id: c for c in citation_map.values() if c.claim_id
        }
        lines: list[str] = [f"# {outline.title}", ""]
        if abstract:
            lines.extend([abstract, ""])
        bib_keys: list[str] = []
        claim_ids: list[str] = []
        for section in sorted(outline.sections, key=lambda s: s.order):
            lines.append(f"## {section.title}")
            lines.append("")
            if not section.claim_ids:
                lines.append(
                    section.notes
                    or f"This section frames {section.title.lower()} for the research objective."
                )
                lines.append("")
                continue
            parts: list[str] = []
            for claim_id in section.claim_ids:
                claim = (claims_by_id or {}).get(claim_id)
                cite = citations_by_claim.get(claim_id)
                if cite is None:
                    continue
                statement = claim.statement if claim is not None else cite.bib.title
                parts.append(f"{statement.rstrip('.')} [@{cite.key}].")
                if cite.key not in bib_keys:
                    bib_keys.append(cite.key)
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
            lines.append(" ".join(parts) if parts else (section.notes or ""))
            lines.append("")
        return MarkdownDraft(
            outline_id=outline.id,
            title=outline.title,
            abstract=abstract,
            markdown="\n".join(lines).strip() + "\n",
            bibliography_keys=bib_keys,
            claim_ids=claim_ids,
        )

    async def build_and_persist(
        self,
        *,
        project_id: str,
        outline: Outline,
        abstract: str | None = None,
    ) -> tuple[StoredDraft, dict[str, CitationKey]]:
        approved = await self.claims.list_by_status(project_id, [ClaimStatus.APPROVED])
        claims_by_id = {c.id: c for c in approved}
        claim_ids = [cid for sec in outline.sections for cid in sec.claim_ids]
        # Include any approved claims referenced by the outline.
        claim_ids = list(dict.fromkeys(claim_ids))
        citations = await self.allocate_citation_keys(project_id=project_id, claim_ids=claim_ids)
        draft = self.build_draft(outline, claims_by_id, citations, abstract=abstract)
        stored = await self.drafts.create(
            draft, project_id=project_id, outline_id=outline.id, status="structured"
        )
        return stored, citations
