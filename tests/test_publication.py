"""Stage 8: outline, writer, citation validation, LaTeX render, and publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeprhetor.domain.enums import ClaimStatus, PublicationStatus, ValidationOutcome
from deeprhetor.domain.knowledge import Evidence, EvidenceLocation, ProposedClaim, quote_content_hash
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
from deeprhetor.domain.writing import CitationKey, DraftSection, StructuredDraft
from deeprhetor.domain.enums import PlanStatus
from deeprhetor.domain.writing import BibEntry
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.writing import OutlineRepository
from deeprhetor.services.citation_validate import CitationValidator, any_code
from deeprhetor.services.latex import LatexRenderer, toolchain_ready
from deeprhetor.services.outline import OutlineBuilderService
from deeprhetor.services.project_store import create_project_async
from deeprhetor.services.publish import PublicationService
from deeprhetor.services.writer import WriterService


async def _seed_project(tmp_path: Path):
    opened = await create_project_async(
        tmp_path / "pub.deeprhetor",
        title="Rhetoric Report",
        prompt="Explain classical rhetoric briefly.",
    )
    docs = DocumentRepository(opened.engine)
    doc, version, segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Aristotle notes",
        segments=["Rhetoric is the faculty of observing in any given case the available means of persuasion."],
        canonical_url="https://example.org/aristotle-rhetoric",
        source_class="web",
    )
    claims = ClaimRepository(opened.engine)
    evidence_repo = EvidenceRepository(opened.engine)
    quote = "available means of persuasion"
    claim = await claims.create(
        ProposedClaim(
            statement="Rhetoric studies the available means of persuasion.",
            topic_id="t_rhetoric",
        ),
        project_id=opened.project.id,
    )
    evidence = await evidence_repo.create(
        Evidence(
            document_id=doc.id,
            document_version_id=version.id,
            document_segment_id=segs[0].id,
            quote=quote,
            location=EvidenceLocation(),
            content_hash=quote_content_hash(quote),
        )
    )
    await claims.attach_evidence(claim.id, evidence.id)
    await claims.transition(claim.id, ClaimStatus.APPROVED)

    plan = ResearchPlan(
        project_id=opened.project.id,
        prompt=opened.project.prompt,
        status=PlanStatus.APPROVED,
        topics=[
            PlanTopic(
                topic_id="t_rhetoric",
                title="Classical Rhetoric",
                objective="Define rhetoric",
                research_angles=["means of persuasion"],
            )
        ],
        sections=[
            PlanSection(
                section_id="sec_body",
                title="Classical Rhetoric",
                topic_ids=["t_rhetoric"],
                questions=["What is rhetoric?"],
                order=0,
            )
        ],
    )
    stored_plan = await ResearchPlanRepository(opened.engine).create(plan)
    plan = stored_plan.plan
    return opened, plan, claim, evidence, doc, version


@pytest.mark.asyncio
async def test_fixture_draft_validates_and_produces_tex_bib_manifest(tmp_path: Path) -> None:
    opened, plan, claim, evidence, doc, version = await _seed_project(tmp_path)
    try:
        outline_svc = OutlineBuilderService(opened.engine)
        stored_outline = await outline_svc.build_and_persist(
            project_id=opened.project.id, plan=plan, title="Classical Rhetoric"
        )
        titles = {s.title.lower() for s in stored_outline.outline.sections}
        assert "introduction" in titles
        assert "conclusion" in titles

        writer = WriterService(opened.engine)
        stored_draft, citations = await writer.build_and_persist(
            project_id=opened.project.id,
            outline=stored_outline.outline,
            abstract="A short abstract.",
        )
        draft = stored_draft.draft
        assert draft.sections
        assert all(isinstance(k, str) for s in draft.sections for k in s.citation_keys)

        pub = PublicationService(opened.engine)
        result = await pub.publish(
            draft,
            project_id=opened.project.id,
            outline=stored_outline.outline,
            citation_map=citations,
            compile_pdf=True,
        )
        assert result.validation_id is not None
        assert result.tex_artifact_id is not None
        assert result.bib_artifact_id is not None
        assert result.manifest_artifact_id is not None
        assert result.validation_report_artifact_id is not None
        assert result.tex is not None and r"\documentclass" in result.tex
        assert result.bib is not None and ("@online" in result.bib or "@misc" in result.bib)
        assert any(c.claim_id == claim.id for c in citations.values())
        assert "Bibliography" in result.tex or "printbibliography" in result.tex
        assert result.manifest is not None
        assert claim.id in result.manifest.claim_ids
        assert evidence.id in result.manifest.evidence_ids
        assert doc.id in result.manifest.document_ids
        assert result.manifest.document_content_hashes.get(doc.id) == version.content_sha256
        assert result.manifest.artifact_ids.get("tex") == result.tex_artifact_id
        assert result.manifest.artifact_ids.get("bib") == result.bib_artifact_id
        assert result.manifest.artifact_ids.get("manifest") == result.manifest_artifact_id
        assert result.status in {PublicationStatus.RENDERED, PublicationStatus.COMPILED}
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_invented_citation_fails_validation(tmp_path: Path) -> None:
    opened, plan, claim, *_rest = await _seed_project(tmp_path)
    try:
        outline_svc = OutlineBuilderService(opened.engine)
        stored_outline = await outline_svc.build_and_persist(
            project_id=opened.project.id, plan=plan
        )
        writer = WriterService(opened.engine)
        stored_draft, citations = await writer.build_and_persist(
            project_id=opened.project.id, outline=stored_outline.outline
        )
        draft = stored_draft.draft.model_copy(
            update={
                "sections": [
                    DraftSection(
                        section_id=s.section_id,
                        title=s.title,
                        prose=s.prose,
                        citation_keys=list(s.citation_keys) + (["invented_cite"] if s.citation_keys else ["invented_cite"]),
                        claim_ids=s.claim_ids,
                        order=s.order,
                    )
                    for s in stored_draft.draft.sections
                ],
                "bibliography_keys": list(stored_draft.draft.bibliography_keys) + ["invented_cite"],
            }
        )
        validator = CitationValidator(opened.engine)
        result = await validator.validate(
            draft,
            project_id=opened.project.id,
            outline=stored_outline.outline,
            citation_map=citations,
            persist=False,
        )
        assert result.outcome == ValidationOutcome.FAILED
        assert any_code(result, {"unresolved_citation"})
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_unsafe_latex_rejected(tmp_path: Path) -> None:
    opened, plan, *_rest = await _seed_project(tmp_path)
    try:
        outline_svc = OutlineBuilderService(opened.engine)
        stored_outline = await outline_svc.build_and_persist(
            project_id=opened.project.id, plan=plan
        )
        writer = WriterService(opened.engine)
        stored_draft, citations = await writer.build_and_persist(
            project_id=opened.project.id, outline=stored_outline.outline
        )
        bad_sections = []
        for s in stored_draft.draft.sections:
            prose = s.prose
            if s.title.lower() == "introduction":
                prose = r"Innocent text \write18{rm -rf /} more text"
            bad_sections.append(
                DraftSection(
                    section_id=s.section_id,
                    title=s.title,
                    prose=prose,
                    citation_keys=s.citation_keys,
                    claim_ids=s.claim_ids,
                    order=s.order,
                )
            )
        draft = stored_draft.draft.model_copy(update={"sections": bad_sections})
        validator = CitationValidator(opened.engine)
        result = await validator.validate(
            draft,
            project_id=opened.project.id,
            outline=stored_outline.outline,
            citation_map=citations,
            persist=False,
        )
        assert result.outcome == ValidationOutcome.FAILED
        assert any_code(result, {"unsafe_latex_shell_escape_write18"})

        # Also reject pipe-input forms.
        pipe = DraftSection(
            section_id=bad_sections[0].section_id,
            title=bad_sections[0].title,
            prose=r'\input{|/bin/sh}',
            citation_keys=[],
            claim_ids=[],
            order=0,
        )
        issues = validator.scan_unsafe_latex(pipe.prose, path="x")
        assert any(i.code.startswith("unsafe_latex_") for i in issues)
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_provenance_manifest_complete(tmp_path: Path) -> None:
    opened, plan, claim, evidence, doc, version = await _seed_project(tmp_path)
    try:
        outline = await OutlineBuilderService(opened.engine).build_and_persist(
            project_id=opened.project.id, plan=plan, title="Manifest Check"
        )
        draft_row, citations = await WriterService(opened.engine).build_and_persist(
            project_id=opened.project.id, outline=outline.outline
        )
        result = await PublicationService(opened.engine).publish(
            draft_row.draft,
            project_id=opened.project.id,
            outline=outline.outline,
            citation_map=citations,
            compile_pdf=False,
        )
        manifest = result.manifest
        assert manifest is not None
        assert manifest.project_id == opened.project.id
        assert manifest.draft_id == draft_row.draft.id
        assert manifest.outline_id == outline.id
        assert manifest.validation_id == result.validation_id
        assert manifest.validation_outcome == ValidationOutcome.PASSED
        assert set(manifest.claim_ids) >= {claim.id}
        assert set(manifest.evidence_ids) >= {evidence.id}
        assert set(manifest.document_ids) >= {doc.id}
        assert manifest.document_content_hashes[doc.id] == version.content_sha256
        assert "tex" in manifest.artifact_ids
        assert "bib" in manifest.artifact_ids
        assert "manifest" in manifest.artifact_ids
        assert "validation_report" in manifest.artifact_ids
        assert "tectonic" in manifest.toolchain
        assert "pandoc" in manifest.toolchain
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_pdf_compile_if_tectonic_available_else_skip(tmp_path: Path) -> None:
    opened, plan, *_rest = await _seed_project(tmp_path)
    try:
        outline = await OutlineBuilderService(opened.engine).build_and_persist(
            project_id=opened.project.id, plan=plan
        )
        draft_row, citations = await WriterService(opened.engine).build_and_persist(
            project_id=opened.project.id, outline=outline.outline
        )
        # Always produce tex/bib even when PDF is skipped.
        result = await PublicationService(opened.engine).publish(
            draft_row.draft,
            project_id=opened.project.id,
            outline=outline.outline,
            citation_map=citations,
            compile_pdf=True,
        )
        assert result.tex and result.bib
        assert result.tex_artifact_id and result.bib_artifact_id and result.manifest_artifact_id

        if not toolchain_ready():
            pytest.skip(
                "PDF compile skipped: tectonic and/or pandoc not installed on this machine"
            )
        assert result.pdf_compiled is True
        assert result.pdf_artifact_id is not None
        assert result.status == PublicationStatus.COMPILED
        assert result.pdf_skipped_reason is None
    finally:
        await opened.dispose()


def test_latex_renderer_escapes_and_uses_template() -> None:
    draft = StructuredDraft(
        outline_id="o1",
        title="A & B",
        sections=[
            DraftSection(
                section_id="sec_introduction",
                title="Introduction",
                prose="Cost is 100% covered.",
                citation_keys=["cite_001"],
                claim_ids=["c1"],
                order=0,
            ),
            DraftSection(
                section_id="sec_conclusion",
                title="Conclusion",
                prose="Done.",
                citation_keys=[],
                claim_ids=[],
                order=1,
            ),
        ],
        bibliography_keys=["cite_001"],
    )
    citations = {
        "cite_001": CitationKey(
            project_id="p",
            key="cite_001",
            claim_id="c1",
            bib=BibEntry(
                key="cite_001",
                entry_type="online",
                title="Source",
                url="https://example.org/x",
            ),
        )
    }
    rendered = LatexRenderer(date_str="2026-08-06").render(draft, citations)
    assert r"A \& B" in rendered.tex
    assert r"100\%" in rendered.tex
    assert r"\footnote{\cite{cite_001}}" in rendered.tex
    assert r"\tableofcontents" in rendered.tex
    assert "@online{cite_001," in rendered.bib
    assert "https://example.org/x" in rendered.bib


@pytest.mark.asyncio
async def test_outline_repository_round_trip(tmp_path: Path) -> None:
    opened, plan, *_rest = await _seed_project(tmp_path)
    try:
        outline = OutlineBuilderService(opened.engine).build_outline(plan, [])
        repo = OutlineRepository(opened.engine)
        stored = await repo.create(outline, project_id=opened.project.id, plan_id=plan.id)
        loaded = await repo.get(stored.id)
        assert loaded is not None
        assert loaded.outline.title == outline.title
        assert len(loaded.outline.sections) == len(outline.sections)
    finally:
        await opened.dispose()
