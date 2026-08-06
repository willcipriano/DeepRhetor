"""Deterministic citation and draft validation before LaTeX render."""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ClaimStatus, ValidationOutcome
from deeprhetor.domain.knowledge import quote_content_hash
from deeprhetor.domain.publication import ValidationIssue, ValidationResult
from deeprhetor.domain.writing import CitationKey, Outline, StructuredDraft
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.repositories.writing import CitationKeyRepository, ValidationResultRepository
from deeprhetor.services.verify import VerifierService

# Unsafe TeX that must never come from model/prose into the compiler.
UNSAFE_LATEX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shell_escape_write18", re.compile(r"\\write\s*18\b", re.IGNORECASE)),
    ("pipe_input", re.compile(r"\\input\s*\{\s*\|")),
    ("immediate_write", re.compile(r"\\immediate\s*\\write", re.IGNORECASE)),
    ("openout", re.compile(r"\\openout\b", re.IGNORECASE)),
    ("closeout", re.compile(r"\\closeout\b", re.IGNORECASE)),
    ("shell_input", re.compile(r"\\input\s*\|")),
    ("catcode", re.compile(r"\\catcode\b", re.IGNORECASE)),
    ("usepackage", re.compile(r"\\usepackage\b", re.IGNORECASE)),
    ("documentclass", re.compile(r"\\documentclass\b", re.IGNORECASE)),
    ("special", re.compile(r"\\special\b", re.IGNORECASE)),
    ("directlua", re.compile(r"\\directlua\b", re.IGNORECASE)),
)

REQUIRED_SECTION_TITLES = frozenset({"introduction", "conclusion"})


class CitationValidator:
    """Validate structured drafts against approved claims, evidence, and bib consistency."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        claims: ClaimRepository | None = None,
        evidence: EvidenceRepository | None = None,
        documents: DocumentRepository | None = None,
        citations: CitationKeyRepository | None = None,
        validations: ValidationResultRepository | None = None,
        verifier: VerifierService | None = None,
    ) -> None:
        self._engine = engine
        self.claims = claims or ClaimRepository(engine)
        self.evidence = evidence or EvidenceRepository(engine)
        self.documents = documents or DocumentRepository(engine)
        self.citations = citations or CitationKeyRepository(engine)
        self.validations = validations or ValidationResultRepository(engine)
        self.verifier = verifier or VerifierService(
            engine, claims=self.claims, evidence=self.evidence, documents=self.documents
        )

    def scan_unsafe_latex(self, text: str, *, path: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for code, pattern in UNSAFE_LATEX_PATTERNS:
            if pattern.search(text):
                issues.append(
                    ValidationIssue(
                        code=f"unsafe_latex_{code}",
                        message=f"Unsafe LaTeX pattern detected ({code})",
                        path=path,
                    )
                )
        return issues

    async def validate(
        self,
        draft: StructuredDraft,
        *,
        project_id: str,
        outline: Outline | None = None,
        citation_map: Mapping[str, CitationKey] | None = None,
        persist: bool = True,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if citation_map is None:
            stored = await self.citations.list_for_project(project_id)
            citation_map = {c.key: c for c in stored}

        # Required sections
        if outline is not None:
            outline_ids = {s.section_id for s in outline.sections}
            draft_ids = {s.section_id for s in draft.sections}
            missing = sorted(outline_ids - draft_ids)
            for sid in missing:
                issues.append(
                    ValidationIssue(
                        code="missing_outline_section",
                        message=f"Draft missing outline section {sid}",
                        path=f"sections.{sid}",
                    )
                )
        titles = {s.title.strip().lower() for s in draft.sections}
        for required in sorted(REQUIRED_SECTION_TITLES):
            if required not in titles:
                issues.append(
                    ValidationIssue(
                        code="missing_required_section",
                        message=f"Required section missing: {required}",
                        path="sections",
                    )
                )
        if not draft.title.strip():
            issues.append(
                ValidationIssue(code="missing_title", message="Draft title is empty", path="title")
            )
        if not draft.sections:
            issues.append(
                ValidationIssue(
                    code="no_sections", message="Draft has no sections", path="sections"
                )
            )

        # Collect cited keys / claims
        cited_keys: set[str] = set()
        for idx, section in enumerate(draft.sections):
            issues.extend(self.scan_unsafe_latex(section.prose, path=f"sections[{idx}].prose"))
            issues.extend(self.scan_unsafe_latex(section.title, path=f"sections[{idx}].title"))
            cited_keys.update(section.citation_keys)
            for claim_id in section.claim_ids:
                claim = await self.claims.get(claim_id)
                if claim is None:
                    issues.append(
                        ValidationIssue(
                            code="unknown_claim",
                            message=f"Claim {claim_id} not found",
                            path=f"sections[{idx}].claim_ids",
                        )
                    )
                elif claim.status != ClaimStatus.APPROVED:
                    issues.append(
                        ValidationIssue(
                            code="claim_not_approved",
                            message=f"Cited claim {claim_id} has status {claim.status}",
                            path=f"sections[{idx}].claim_ids",
                        )
                    )

        if draft.abstract:
            issues.extend(self.scan_unsafe_latex(draft.abstract, path="abstract"))

        for key in sorted(cited_keys | set(draft.bibliography_keys)):
            citation = citation_map.get(key)
            if citation is None:
                issues.append(
                    ValidationIssue(
                        code="unresolved_citation",
                        message=f"Citation key {key!r} does not resolve",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            # Bib consistency
            issues.extend(_check_bib_consistency(citation, path=f"citation_keys.{key}"))

            if not citation.claim_id:
                issues.append(
                    ValidationIssue(
                        code="citation_missing_claim",
                        message=f"Citation {key} is not bound to a claim",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            claim = await self.claims.get(citation.claim_id)
            if claim is None:
                issues.append(
                    ValidationIssue(
                        code="citation_claim_missing",
                        message=f"Citation {key} claims unknown id {citation.claim_id}",
                        path=f"citation_keys.{key}",
                    )
                )
                continue
            if claim.status != ClaimStatus.APPROVED:
                issues.append(
                    ValidationIssue(
                        code="citation_claim_not_approved",
                        message=f"Citation {key} bound to non-approved claim",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            if not citation.evidence_id:
                issues.append(
                    ValidationIssue(
                        code="invented_source",
                        message=f"Citation {key} has no evidence (invented source)",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            evidence = await self.evidence.get(citation.evidence_id)
            if evidence is None:
                issues.append(
                    ValidationIssue(
                        code="evidence_missing",
                        message=f"Evidence {citation.evidence_id} missing for {key}",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            if evidence.content_hash != quote_content_hash(evidence.quote):
                issues.append(
                    ValidationIssue(
                        code="invented_quotation",
                        message=f"Evidence hash mismatch for citation {key}",
                        path=f"citation_keys.{key}",
                    )
                )

            version = await self.documents.get_version(evidence.document_version_id)
            if version is None:
                issues.append(
                    ValidationIssue(
                        code="document_version_missing",
                        message=f"Archived version missing for citation {key}",
                        path=f"citation_keys.{key}",
                    )
                )
                continue

            if (
                citation.document_content_sha256
                and citation.document_content_sha256 != version.content_sha256
            ):
                issues.append(
                    ValidationIssue(
                        code="document_hash_mismatch",
                        message=f"Archived document hash changed for citation {key}",
                        path=f"citation_keys.{key}",
                    )
                )

            # Quote still matches archived location / text
            normalized = await self.verifier.get_normalized_text(evidence.document_version_id)
            segment_text = None
            if evidence.document_segment_id:
                seg = await self.verifier.get_segment(evidence.document_segment_id)
                segment_text = seg.text if seg else None
            check = self.verifier.check_quote_against_text(
                evidence, normalized_text=normalized, segment_text=segment_text
            )
            if not check.ok:
                issues.append(
                    ValidationIssue(
                        code="evidence_location_mismatch",
                        message=(
                            f"Cited evidence for {key} no longer matches archive: "
                            + ", ".join(check.failures)
                        ),
                        path=f"citation_keys.{key}",
                    )
                )

        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        if errors:
            outcome = ValidationOutcome.FAILED
        elif warnings:
            outcome = ValidationOutcome.WARNINGS
        else:
            outcome = ValidationOutcome.PASSED

        result = ValidationResult(draft_id=draft.id, outcome=outcome, issues=issues)
        if persist:
            result = await self.validations.create(result)
        return result


def _check_bib_consistency(citation: CitationKey, *, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    bib = citation.bib
    if bib.key and bib.key != citation.key:
        issues.append(
            ValidationIssue(
                code="bib_key_mismatch",
                message=f"Bib entry key {bib.key!r} != citation key {citation.key!r}",
                path=path,
            )
        )
    if not bib.title:
        issues.append(
            ValidationIssue(code="bib_missing_title", message="Bibliography title missing", path=path)
        )
    if bib.entry_type == "online" and not bib.url:
        issues.append(
            ValidationIssue(
                code="bib_online_missing_url",
                message="@online entry requires url",
                path=path,
            )
        )
    if bib.doi and " " in bib.doi:
        issues.append(
            ValidationIssue(code="bib_invalid_doi", message="DOI contains whitespace", path=path)
        )
    return issues


def collect_issue_codes(result: ValidationResult) -> set[str]:
    return {i.code for i in result.issues}


def any_code(result: ValidationResult, codes: Iterable[str]) -> bool:
    wanted = set(codes)
    return bool(collect_issue_codes(result) & wanted)
