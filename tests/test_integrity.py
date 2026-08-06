"""Stage 10 automated integrity suite.

Documents PLAN “Testing and evaluation” coverage and adds high-value gap tests
that are thin or missing elsewhere. Prefer this module as the checklist map;
existing stage files remain the primary homes for deep coverage.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from deeprhetor.db import SCHEMA_VERSION, apply_migrations, create_sync_engine_for_path
from deeprhetor.domain.enums import (
    ClaimStatus,
    EvidenceDirectness,
    EvidenceRelation,
    PublicationStatus,
    RunStatus,
    TaskStatus,
    ValidationOutcome,
)
from deeprhetor.domain.knowledge import (
    ClaimRelation,
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from deeprhetor.domain.sources import FetchRequest, RawDocument
from deeprhetor.domain.writing import DraftSection, StructuredDraft
from deeprhetor.models.agents import agent_tool_names, build_role_agent
from deeprhetor.models.roles import (
    WRITER_FORBIDDEN_TOOL_PATTERNS,
    WRITER_TOOLS,
    RoleName,
)
from deeprhetor.plugins.parsers import ParserRegistry, guess_media_type
from deeprhetor.repositories import ArtifactRepository, RunRepository, TaskRepository
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import (
    ClaimRepository,
    ClaimTransitionError,
    EvidenceRepository,
)
from deeprhetor.repositories.scan import ScanRepository
from deeprhetor.services.citation_validate import CitationValidator, any_code
from deeprhetor.services.fetch import FetchError, SecureHttpFetcher, validate_public_url
from deeprhetor.services.fts import FtsService
from deeprhetor.services.latex import LatexRenderer
from deeprhetor.services.local_import import LocalFileImporter
from deeprhetor.services.outline import OutlineBuilderService
from deeprhetor.services.project_store import (
    backup_project,
    create_project_async,
    open_project_async,
)
from deeprhetor.services.publish import PublicationService
from deeprhetor.services.recovery import RecoveryService
from deeprhetor.services.scan import ScanService
from deeprhetor.services.verify import VerifierService
from deeprhetor.services.writer import WriterService

# ---------------------------------------------------------------------------
# Coverage checklist (PLAN “Testing and evaluation”)
# ---------------------------------------------------------------------------
# Each entry maps a required automated area to primary test modules/functions.
# Gap tests below fill thin spots called out for Stage 10.
INTEGRITY_COVERAGE: dict[str, tuple[str, ...]] = {
    "schema_migrations_and_repository_state": (
        "tests/test_migrations.py::test_migrations_apply_to_temp_sqlite",
        "tests/test_knowledge.py::test_claim_state_transitions",
        "tests/test_integrity.py::test_migrations_schema_version_and_core_tables",
        "tests/test_integrity.py::test_illegal_claim_transition_rejected",
    ),
    "single_file_project_create_backup_reopen_recovery": (
        "tests/test_persistence.py::test_create_reopen_backup_roundtrip",
        "tests/test_persistence.py::test_interrupted_run_recovery_on_open",
        "tests/test_cli.py::test_project_create_and_backup",
        "tests/test_integrity.py::test_project_backup_reopen_preserves_prompt",
    ),
    "node_replay_and_idempotency": (
        "tests/test_persistence.py::test_idempotent_task_and_artifact_inserts",
        "tests/test_workflow.py::test_checkpoint_kill_resume_no_duplicate_tasks",
        "tests/test_integrity.py::test_artifact_idempotency_preserves_original_bytes",
    ),
    "interrupted_run_resumption": (
        "tests/test_persistence.py::test_interrupted_run_recovery_on_open",
        "tests/test_workflow.py::test_checkpoint_kill_resume_no_duplicate_tasks",
        "tests/test_web.py::test_recovery_screen_for_interrupted_runs",
        "tests/test_integrity.py::test_scan_resumes_after_open_recovery",
    ),
    "provider_contract_fixtures": (
        "tests/test_sources_stage6.py (tavily/openalex/crossref/arxiv fixtures)",
        "tests/test_mediawiki.py",
        "tests/test_protocols.py",
    ),
    "url_ssrf_and_file_size_protections": (
        "tests/test_ssrf.py",
        "tests/test_integrity.py::test_fetch_rejects_oversized_content_length",
        "tests/test_integrity.py::test_fetch_rejects_stream_exceeding_max_bytes",
        "tests/test_integrity.py::test_local_import_rejects_oversized_file",
    ),
    "parser_fixtures_every_supported_format": (
        "tests/test_parsers.py (txt/md/html/pdf/docx)",
        "tests/test_integrity.py::test_parser_registry_covers_fixture_formats",
    ),
    "ocr_and_headless_browser_fallback": (
        "tests/test_sources_stage6.py::test_acquisition_falls_back_to_playwright",
        "tests/test_sources_stage6.py::test_pdf_ocr_graceful_skip_when_tesseract_missing",
    ),
    "complete_segment_scan_accounting": (
        "tests/test_knowledge.py::test_scan_incomplete_until_all_segments_terminal",
        "tests/test_integrity.py::test_scan_failed_segment_counted_as_terminal",
    ),
    "fts5_retrieval": (
        "tests/test_persistence.py::test_fts_index_and_search",
        "tests/test_integrity.py::test_fts_rebuild_after_clear_document_version",
    ),
    "exact_quote_and_citation_span_validation": (
        "tests/test_knowledge.py::test_bad_quote_fails_verification",
        "tests/test_publication.py::test_invented_citation_fails_validation",
        "tests/test_integrity.py::test_quote_span_and_hash_validation_failures",
        "tests/test_integrity.py::test_citation_validator_rejects_wrong_char_span",
    ),
    "claim_evidence_relationship_integrity": (
        "tests/test_integrity.py::test_attach_evidence_upsert_and_list_for_claim",
        "tests/test_integrity.py::test_record_claim_relation_persisted",
        "tests/test_integrity.py::test_attach_evidence_unknown_claim_raises",
        "tests/test_integrity.py::test_evidence_reject_mismatched_content_hash",
    ),
    "model_output_validation_and_writer_tool_restrictions": (
        "tests/test_models.py::test_writer_tool_surface_excludes_search_fetch_approve",
        "tests/test_models.py::test_structured_output_validation_with_test_model",
        "tests/test_integrity.py::test_writer_tool_surface_closed_set",
    ),
    "safe_latex_generation_and_pdf": (
        "tests/test_publication.py::test_unsafe_latex_rejected",
        "tests/test_publication.py::test_latex_renderer_escapes_and_uses_template",
        "tests/test_publication.py::test_pdf_compile_if_tectonic_available_else_skip",
        "tests/test_integrity.py::test_compile_pdf_disables_shell_escape_env",
    ),
    "provenance_manifest_completeness": (
        "tests/test_publication.py::test_provenance_manifest_complete",
        "tests/test_integrity.py::test_failed_publish_omits_success_manifest",
    ),
}


def test_integrity_coverage_checklist_is_documented() -> None:
    """Checklist stays non-empty and covers every PLAN automated area."""
    required = {
        "schema_migrations_and_repository_state",
        "single_file_project_create_backup_reopen_recovery",
        "node_replay_and_idempotency",
        "interrupted_run_resumption",
        "provider_contract_fixtures",
        "url_ssrf_and_file_size_protections",
        "parser_fixtures_every_supported_format",
        "ocr_and_headless_browser_fallback",
        "complete_segment_scan_accounting",
        "fts5_retrieval",
        "exact_quote_and_citation_span_validation",
        "claim_evidence_relationship_integrity",
        "model_output_validation_and_writer_tool_restrictions",
        "safe_latex_generation_and_pdf",
        "provenance_manifest_completeness",
    }
    assert set(INTEGRITY_COVERAGE) == required
    for key, refs in INTEGRITY_COVERAGE.items():
        assert refs, f"empty coverage for {key}"


# ---------------------------------------------------------------------------
# Schema / repository state
# ---------------------------------------------------------------------------


def test_migrations_schema_version_and_core_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "integrity.db"
    apply_migrations(db_path)
    engine = create_sync_engine_for_path(db_path)
    with engine.connect() as conn:
        version = conn.execute(
            text("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        ).scalar_one()
        tables = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    engine.dispose()
    assert version == SCHEMA_VERSION
    for name in ("project", "run", "task", "claim", "evidence", "claim_evidence", "claim_relation"):
        assert name in tables


@pytest.mark.asyncio
async def test_illegal_claim_transition_rejected(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "claim.deeprhetor", title="C", prompt="p")
    try:
        claims = ClaimRepository(opened.engine)
        stored = await claims.create(
            ProposedClaim(statement="Terminal claim"),
            project_id=opened.project.id,
        )
        await claims.transition(stored.id, ClaimStatus.REJECTED)
        with pytest.raises(ClaimTransitionError):
            await claims.transition(stored.id, ClaimStatus.APPROVED)
    finally:
        await opened.dispose()


# ---------------------------------------------------------------------------
# Project lifecycle / idempotency / recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_backup_reopen_preserves_prompt(tmp_path: Path) -> None:
    src = tmp_path / "src.deeprhetor"
    opened = await create_project_async(src, title="Backup", prompt="integrity prompt")
    project_id = opened.project.id
    await opened.dispose()

    dest = tmp_path / "backup.deeprhetor"
    backup_project(src, dest)
    backed = await open_project_async(dest)
    try:
        assert backed.project.id == project_id
        assert backed.project.prompt == "integrity prompt"
        assert backed.project.title == "Backup"
    finally:
        await backed.dispose()


@pytest.mark.asyncio
async def test_artifact_idempotency_preserves_original_bytes(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "idem.deeprhetor", title="I", prompt="p")
    try:
        artifacts = ArtifactRepository(opened.engine)
        first = await artifacts.create(
            project_id=opened.project.id,
            kind="manifest",
            path_or_name="m.json",
            data=b'{"v":1}',
            idempotency_key="artifact:integrity:v1",
        )
        second = await artifacts.create(
            project_id=opened.project.id,
            kind="manifest",
            path_or_name="m.json",
            data=b'{"v":2}',
            idempotency_key="artifact:integrity:v1",
        )
        assert first.id == second.id
        assert await artifacts.get_data(first.id) == b'{"v":1}'
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_scan_resumes_after_open_recovery(tmp_path: Path) -> None:
    path = tmp_path / "resume-scan.deeprhetor"
    opened = await create_project_async(path, title="R", prompt="p")
    docs = DocumentRepository(opened.engine)
    doc, version, _segs = await docs.create_with_version_and_segments(
        project_id=opened.project.id,
        title="Notes",
        segments=["alpha", "beta", "gamma"],
    )
    scanner = ScanService(opened.engine, batch_size=1)
    batch = await scanner.scan_batch(
        document_id=doc.id, document_version_id=version.id, batch_index=0
    )
    assert batch.document_scan.is_complete is False
    assert batch.remaining == 2

    runs = RunRepository(opened.engine)
    tasks = TaskRepository(opened.engine)
    run = await runs.create(project_id=opened.project.id, status=RunStatus.RUNNING)
    task = await tasks.create(
        run_id=run.id,
        kind="scan",
        status=TaskStatus.RUNNING,
        idempotency_key="scan-resume-1",
    )
    await opened.dispose()

    reopened = await open_project_async(path)
    try:
        assert reopened.recovery is not None
        assert run.id in reopened.recovery.interrupted_run_ids
        recovery = RecoveryService(reopened.engine)
        await recovery.resume_run(run.id)
        await recovery.retry_task(task.id)

        scanner2 = ScanService(reopened.engine, batch_size=2)
        final = await scanner2.scan_until_complete(
            document_id=doc.id, document_version_id=version.id
        )
        assert final.is_complete is True
        assert final.total_segments == 3
        remaining = await ScanRepository(reopened.engine).list_unscanned_segment_ids(version.id)
        assert remaining == []
    finally:
        await reopened.dispose()


# ---------------------------------------------------------------------------
# SSRF / file-size
# ---------------------------------------------------------------------------


def test_validate_public_url_still_blocks_loopback() -> None:
    with pytest.raises(Exception):
        validate_public_url("http://127.0.0.1/")


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "1000"},
            content=b"x" * 10,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = SecureHttpFetcher(client=client, max_bytes=100)
        with pytest.raises(FetchError, match="content-length"):
            await fetcher.fetch(FetchRequest(url="http://1.1.1.1/big"))


@pytest.mark.asyncio
async def test_fetch_rejects_stream_exceeding_max_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 200,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = SecureHttpFetcher(client=client, max_bytes=50)
        with pytest.raises(FetchError, match="max_bytes"):
            await fetcher.fetch(FetchRequest(url="http://1.1.1.1/stream"))


@pytest.mark.asyncio
async def test_local_import_rejects_oversized_file(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "size.deeprhetor", title="S", prompt="p")
    try:
        big = tmp_path / "big.txt"
        big.write_bytes(b"a" * 200)
        importer = LocalFileImporter(opened.engine)
        with pytest.raises(ValueError, match="max_bytes"):
            await importer.import_path(big, project_id=opened.project.id, max_bytes=50)
    finally:
        await opened.dispose()


# ---------------------------------------------------------------------------
# Parsers / scan / FTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parser_registry_covers_fixture_formats(repo_root: Path) -> None:
    fixtures = repo_root / "tests" / "fixtures" / "documents"
    registry = ParserRegistry()
    expected = {
        "sample.txt": "text/plain",
        "sample.md": "text/markdown",
        "sample.html": "text/html",
        "sample.pdf": "application/pdf",
        "sample.docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    }
    for name, media in expected.items():
        path = fixtures / name
        assert path.is_file(), f"missing fixture {name}"
        assert guess_media_type(name) == media
        raw = RawDocument(content=path.read_bytes(), media_type=media, filename=name)
        parsed = await registry.parse(raw)
        assert parsed.text.strip()
        assert parsed.segments


@pytest.mark.asyncio
async def test_scan_failed_segment_counted_as_terminal(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "scan-fail.deeprhetor", title="S", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        doc, version, _segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="Fail notes",
            segments=["one", "two"],
        )

        async def failing_handler(segment, batch_index: int) -> dict:
            if segment.segment_index == 0:
                return {"status": "failed", "warnings": ["boom"]}
            return {"status": "completed"}

        scanner = ScanService(opened.engine, batch_size=10)
        result = await scanner.scan_until_complete(
            document_id=doc.id,
            document_version_id=version.id,
            handler=failing_handler,
        )
        assert result.is_complete is True
        assert result.failed_segments == 1
        assert result.completed_segments == 1
        assert result.total_segments == 2
        remaining = await ScanRepository(opened.engine).list_unscanned_segment_ids(version.id)
        assert remaining == []
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_fts_rebuild_after_clear_document_version(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "fts.deeprhetor", title="F", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        _doc, version, _segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="Boiling",
            segments=["Water boils at one hundred degrees Celsius. The boiling point depends on pressure."],
        )
        fts = FtsService(opened.engine)
        assert await fts.search_documents("boiling")
        await fts.clear_document_version(version.id)
        assert await fts.search_documents("boiling") == []
        rebuilt = await fts.index_document_version(version.id)
        assert rebuilt >= 1
        hits = await fts.search_documents("boiling")
        assert hits
        assert hits[0].document_version_id == version.id
    finally:
        await opened.dispose()


# ---------------------------------------------------------------------------
# Quote / citation / claim–evidence integrity
# ---------------------------------------------------------------------------


def test_quote_span_and_hash_validation_failures() -> None:
    verifier = VerifierService.__new__(VerifierService)
    text_value = "The archived sentence is exactly this."
    quote = "exactly this"
    start = text_value.index(quote)

    bad_span = Evidence(
        document_id="d",
        document_version_id="v",
        quote=quote,
        location=EvidenceLocation(char_start=0, char_end=5),
        content_hash=quote_content_hash(quote),
    )
    span_check = verifier.check_quote_against_text(bad_span, normalized_text=text_value)
    assert span_check.ok is False
    assert "quote_does_not_match_location_span" in span_check.failures

    bad_hash = Evidence(
        document_id="d",
        document_version_id="v",
        quote=quote,
        location=EvidenceLocation(char_start=start, char_end=start + len(quote)),
        content_hash="0" * 64,
    )
    hash_check = verifier.check_quote_against_text(bad_hash, normalized_text=text_value)
    assert hash_check.ok is False
    assert "content_hash_mismatch" in hash_check.failures


@pytest.mark.asyncio
async def test_citation_validator_rejects_wrong_char_span(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "cite-span.deeprhetor", title="C", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        doc, version, segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="Source",
            segments=["The archived sentence is exactly this."],
            canonical_url="https://example.org/source",
            source_class="web",
        )
        claims = ClaimRepository(opened.engine)
        evidence_repo = EvidenceRepository(opened.engine)
        quote = "exactly this"
        claim = await claims.create(
            ProposedClaim(statement="Matches archive", topic_id="t1"),
            project_id=opened.project.id,
        )
        evidence = await evidence_repo.create(
            Evidence(
                document_id=doc.id,
                document_version_id=version.id,
                document_segment_id=segs[0].id,
                quote=quote,
                location=EvidenceLocation(char_start=0, char_end=5),
                content_hash=quote_content_hash(quote),
            )
        )
        await claims.attach_evidence(claim.id, evidence.id)
        await claims.transition(claim.id, ClaimStatus.APPROVED)

        from deeprhetor.domain.enums import PlanStatus
        from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
        from deeprhetor.repositories.planning import ResearchPlanRepository

        plan = ResearchPlan(
            project_id=opened.project.id,
            prompt=opened.project.prompt,
            status=PlanStatus.APPROVED,
            topics=[PlanTopic(topic_id="t1", title="T", objective="o")],
            sections=[
                PlanSection(section_id="sec_body", title="Body", topic_ids=["t1"], order=0)
            ],
        )
        stored_plan = await ResearchPlanRepository(opened.engine).create(plan)
        outline = await OutlineBuilderService(opened.engine).build_and_persist(
            project_id=opened.project.id, plan=stored_plan.plan
        )
        draft_row, citations = await WriterService(opened.engine).build_and_persist(
            project_id=opened.project.id, outline=outline.outline
        )
        # Force citation evidence location mismatch by swapping to bad evidence in place.
        validator = CitationValidator(opened.engine)
        result = await validator.validate(
            draft_row.draft,
            project_id=opened.project.id,
            outline=outline.outline,
            citation_map=citations,
            persist=False,
        )
        assert result.outcome == ValidationOutcome.FAILED
        assert any_code(result, {"evidence_location_mismatch"})
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_attach_evidence_upsert_and_list_for_claim(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "link.deeprhetor", title="L", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        doc, version, segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="S",
            segments=["quote text here"],
        )
        claims = ClaimRepository(opened.engine)
        evidence_repo = EvidenceRepository(opened.engine)
        claim = await claims.create(
            ProposedClaim(statement="Linked claim"),
            project_id=opened.project.id,
        )
        evidence = await evidence_repo.create(
            Evidence(
                document_id=doc.id,
                document_version_id=version.id,
                document_segment_id=segs[0].id,
                quote="quote text here",
                location=EvidenceLocation(),
                content_hash=quote_content_hash("quote text here"),
            )
        )
        await claims.attach_evidence(
            claim.id,
            evidence.id,
            relation=EvidenceRelation.SUPPORTS,
            directness=EvidenceDirectness.DIRECT,
            explanation="first",
        )
        await claims.attach_evidence(
            claim.id,
            evidence.id,
            relation=EvidenceRelation.QUALIFIES,
            directness=EvidenceDirectness.INDIRECT,
            explanation="updated",
        )
        pairs = await evidence_repo.list_for_claim(claim.id)
        assert len(pairs) == 1
        ev, link = pairs[0]
        assert ev.id == evidence.id
        assert link.relation == EvidenceRelation.QUALIFIES
        assert link.directness == EvidenceDirectness.INDIRECT
        assert link.explanation == "updated"
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_attach_evidence_unknown_claim_raises(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "missing.deeprhetor", title="M", prompt="p")
    try:
        claims = ClaimRepository(opened.engine)
        with pytest.raises(LookupError, match="claim not found"):
            await claims.attach_evidence("missing-claim", "missing-evidence")
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_record_claim_relation_persisted(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "rel.deeprhetor", title="R", prompt="p")
    try:
        claims = ClaimRepository(opened.engine)
        a = await claims.create(ProposedClaim(statement="A"), project_id=opened.project.id)
        b = await claims.create(ProposedClaim(statement="B"), project_id=opened.project.id)
        await claims.record_relation(
            ClaimRelation(
                from_claim_id=a.id,
                to_claim_id=b.id,
                relation="tension",
                notes="competing framing",
            )
        )
        async with opened.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT from_claim_id, to_claim_id, relation, notes "
                        "FROM claim_relation WHERE from_claim_id = :a"
                    ),
                    {"a": a.id},
                )
            ).mappings().one()
        assert row["to_claim_id"] == b.id
        assert row["relation"] == "tension"
        assert row["notes"] == "competing framing"
    finally:
        await opened.dispose()


@pytest.mark.asyncio
async def test_evidence_reject_mismatched_content_hash(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "hash.deeprhetor", title="H", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        doc, version, segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="S",
            segments=["hello"],
        )
        evidence_repo = EvidenceRepository(opened.engine)
        with pytest.raises(ValueError, match="content_hash"):
            await evidence_repo.create(
                Evidence(
                    document_id=doc.id,
                    document_version_id=version.id,
                    document_segment_id=segs[0].id,
                    quote="hello",
                    location=EvidenceLocation(),
                    content_hash="deadbeef",
                )
            )
    finally:
        await opened.dispose()


# ---------------------------------------------------------------------------
# Writer tools / LaTeX / provenance
# ---------------------------------------------------------------------------


def test_writer_tool_surface_closed_set() -> None:
    writer = build_role_agent(RoleName.WRITER, use_test_model=True)
    names = agent_tool_names(writer)
    assert names == frozenset(WRITER_TOOLS)
    for forbidden in WRITER_FORBIDDEN_TOOL_PATTERNS:
        assert forbidden not in names


def test_compile_pdf_disables_shell_escape_env(tmp_path: Path, monkeypatch) -> None:
    rendered = LatexRenderer().render(
        StructuredDraft(
            outline_id="o1",
            title="Safe",
            sections=[
                DraftSection(
                    section_id="sec_introduction",
                    title="Introduction",
                    prose="Plain prose.",
                    citation_keys=[],
                    claim_ids=[],
                    order=0,
                )
            ],
            bibliography_keys=[],
        ),
        {},
    )
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"cmd": list(cmd), "env": dict(kwargs.get("env") or {})})

        class Proc:
            returncode = 1
            stdout = ""
            stderr = "forced fail"

        return Proc()

    monkeypatch.setattr(
        "deeprhetor.services.latex.which_toolchain",
        lambda: {"pandoc": "pandoc", "tectonic": "tectonic"},
    )
    monkeypatch.setattr("deeprhetor.services.latex.subprocess.run", fake_run)

    result = LatexRenderer().compile_pdf(rendered, work_dir=tmp_path / "tex")
    assert result.pdf_path is None
    assert calls
    assert "TEXINPUTS_shell_escape" not in calls[0]["env"]
    # Pandoc first; no -shell-escape style flags on either command.
    for call in calls:
        joined = " ".join(str(c) for c in call["cmd"]).lower()
        assert "shell-escape" not in joined
        assert "shell_escape" not in joined
        assert "write18" not in joined


@pytest.mark.asyncio
async def test_failed_publish_omits_success_manifest(tmp_path: Path) -> None:
    opened = await create_project_async(tmp_path / "pub-fail.deeprhetor", title="P", prompt="p")
    try:
        docs = DocumentRepository(opened.engine)
        doc, version, segs = await docs.create_with_version_and_segments(
            project_id=opened.project.id,
            title="Aristotle notes",
            segments=[
                "Rhetoric is the faculty of observing in any given case "
                "the available means of persuasion."
            ],
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

        from deeprhetor.domain.enums import PlanStatus
        from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
        from deeprhetor.repositories.planning import ResearchPlanRepository

        plan = ResearchPlan(
            project_id=opened.project.id,
            prompt=opened.project.prompt,
            status=PlanStatus.APPROVED,
            topics=[
                PlanTopic(
                    topic_id="t_rhetoric",
                    title="Classical Rhetoric",
                    objective="Define rhetoric",
                )
            ],
            sections=[
                PlanSection(
                    section_id="sec_body",
                    title="Classical Rhetoric",
                    topic_ids=["t_rhetoric"],
                    order=0,
                )
            ],
        )
        stored_plan = await ResearchPlanRepository(opened.engine).create(plan)
        outline = await OutlineBuilderService(opened.engine).build_and_persist(
            project_id=opened.project.id, plan=stored_plan.plan
        )
        draft_row, citations = await WriterService(opened.engine).build_and_persist(
            project_id=opened.project.id, outline=outline.outline
        )
        bad_draft = draft_row.draft.model_copy(
            update={
                "sections": [
                    DraftSection(
                        section_id=s.section_id,
                        title=s.title,
                        prose=s.prose,
                        citation_keys=list(s.citation_keys) + ["invented_cite"],
                        claim_ids=s.claim_ids,
                        order=s.order,
                    )
                    for s in draft_row.draft.sections
                ],
                "bibliography_keys": list(draft_row.draft.bibliography_keys)
                + ["invented_cite"],
            }
        )
        result = await PublicationService(opened.engine).publish(
            bad_draft,
            project_id=opened.project.id,
            outline=outline.outline,
            citation_map=citations,
            compile_pdf=False,
        )
        assert result.status == PublicationStatus.FAILED
        assert result.manifest is None
        assert result.tex_artifact_id is None
        assert result.pdf_artifact_id is None
        assert result.manifest_artifact_id is None
        assert result.validation_report_artifact_id is not None
    finally:
        await opened.dispose()
