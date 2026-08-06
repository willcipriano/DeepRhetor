"""Markdown draft + AI/deterministic typesetting publication path."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeprhetor.domain.enums import ClaimStatus, PlanStatus, PublicationStatus, ValidationOutcome
from deeprhetor.domain.knowledge import Evidence, EvidenceLocation, ProposedClaim, quote_content_hash
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
from deeprhetor.domain.writing import MarkdownDraft
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.writing import DraftRepository
from deeprhetor.services.citation_validate import (
    CitationValidator,
    extract_markdown_citation_keys,
)
from deeprhetor.services.llm_typeset import LatexTypesetter, deterministic_markdown_typeset
from deeprhetor.services.outline import OutlineBuilderService
from deeprhetor.services.project_store import create_project_async
from deeprhetor.services.publish import PublicationService
from deeprhetor.services.writer import WriterService


def test_extract_markdown_citation_keys() -> None:
    md = "Aristotle founded formal logic [@cite_001]. Also see [^cite_002]."
    assert extract_markdown_citation_keys(md) == {"cite_001", "cite_002"}


def test_deterministic_markdown_typeset_converts_cites() -> None:
    draft = MarkdownDraft(
        outline_id="o1",
        title="Demo",
        abstract="Abs",
        markdown="# Demo\n\n## Introduction\n\nHello [@cite_001].\n\n## Conclusion\n\nDone.\n",
        bibliography_keys=["cite_001"],
    )
    typeset = deterministic_markdown_typeset(draft, allowed_keys={"cite_001"})
    assert typeset.title == "Demo"
    bodies = " ".join(s.body_latex for s in typeset.sections)
    assert r"\footnote{\cite{cite_001}}" in bodies
    assert "[@cite_001]" not in bodies


@pytest.mark.asyncio
async def test_publish_markdown_path(tmp_path: Path) -> None:
    opened = await create_project_async(
        tmp_path / "md.deeprhetor",
        title="Logic",
        prompt="Prove Aristotle invented formal logic.",
    )
    docs = DocumentRepository(opened.engine)
    doc, version, segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Organon",
        segments=["Aristotle's Organon founded the traditional study of formal logic."],
        canonical_url="https://example.org/organon",
    )
    claims = ClaimRepository(opened.engine)
    evidence_repo = EvidenceRepository(opened.engine)
    quote = "founded the traditional study of formal logic"
    claim = await claims.create(
        ProposedClaim(statement="Aristotle founded formal logic via the Organon.", topic_id="t1"),
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
        topics=[PlanTopic(topic_id="t1", title="Organon", objective="Logic")],
        sections=[
            PlanSection(
                section_id="sec_body",
                title="Aristotle and Formal Logic",
                topic_ids=["t1"],
                order=1,
            )
        ],
    )
    await ResearchPlanRepository(opened.engine).create(plan)
    outline = await OutlineBuilderService(opened.engine).build_and_persist(
        project_id=opened.project.id, plan=plan, title="Aristotle invented formal logic"
    )
    writer = WriterService(opened.engine)
    citations = await writer.allocate_citation_keys(
        project_id=opened.project.id, claim_ids=[claim.id]
    )
    key = next(iter(citations))
    md = MarkdownDraft(
        outline_id=outline.id,
        title="Aristotle invented formal logic",
        abstract="A short abstract.",
        markdown=(
            "# Aristotle invented formal logic\n\n"
            "## Introduction\n\n"
            "This report supports the thesis.\n\n"
            "## Aristotle and Formal Logic\n\n"
            f"Archived sources show Aristotle founded formal logic [@{key}].\n\n"
            "## Conclusion\n\n"
            "The evidence supports the thesis.\n"
        ),
        bibliography_keys=[key],
        claim_ids=[claim.id],
    )
    stored = await DraftRepository(opened.engine).create(
        md, project_id=opened.project.id, outline_id=outline.id, status="markdown"
    )
    assert stored.markdown_draft is not None
    md = stored.markdown_draft

    validation = await CitationValidator(opened.engine).validate_markdown(
        md,
        project_id=opened.project.id,
        outline=outline.outline,
        citation_map=citations,
        persist=False,
    )
    assert validation.outcome == ValidationOutcome.PASSED

    pub = PublicationService(
        opened.engine,
        typesetter=LatexTypesetter(config=None),  # force deterministic typeset
    )
    result = await pub.publish_markdown(
        md,
        project_id=opened.project.id,
        outline=outline.outline,
        citation_map=citations,
        compile_pdf=False,
    )
    assert result.status == PublicationStatus.RENDERED
    assert result.markdown_artifact_id
    assert result.tex and r"\cite{" in result.tex
    assert result.bib and key in result.bib
    assert "\\documentclass" in (result.tex or "")
    await opened.engine.dispose()
