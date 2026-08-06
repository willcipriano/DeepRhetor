"""Deterministic claim evidence verification against archived text."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ClaimStatus, VerificationDecisionKind
from deeprhetor.domain.knowledge import (
    Evidence,
    VerificationDecision,
    quote_content_hash,
)
from deeprhetor.repositories.document import DocumentRepository, DocumentSegment
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository


@dataclass
class QuoteCheckResult:
    ok: bool
    evidence_id: str
    failures: list[str]


class VerifierService:
    """Approve/reject claims using deterministic quote-span checks."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        claims: ClaimRepository | None = None,
        evidence: EvidenceRepository | None = None,
        documents: DocumentRepository | None = None,
    ) -> None:
        self._engine = engine
        self.claims = claims or ClaimRepository(engine)
        self.evidence = evidence or EvidenceRepository(engine)
        self.documents = documents or DocumentRepository(engine)

    async def get_normalized_text(self, document_version_id: str) -> str:
        segments = await self.documents.list_segments(document_version_id)
        return "\n\n".join(s.text for s in segments)

    async def get_segment(self, segment_id: str) -> DocumentSegment | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, document_version_id, segment_index, text, page, "
                    "section_path, char_start, char_end, status "
                    "FROM document_segment WHERE id = :id"
                ),
                {"id": segment_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return DocumentSegment(
            id=row["id"],
            document_version_id=row["document_version_id"],
            segment_index=row["segment_index"],
            text=row["text"],
            page=row["page"],
            section_path=row["section_path"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            status=row["status"],
        )

    def check_quote_against_text(
        self,
        evidence: Evidence,
        *,
        normalized_text: str,
        segment_text: str | None = None,
    ) -> QuoteCheckResult:
        failures: list[str] = []
        expected_hash = quote_content_hash(evidence.quote)
        if evidence.content_hash != expected_hash:
            failures.append("content_hash_mismatch")
        if not evidence.quote:
            failures.append("empty_quote")
            return QuoteCheckResult(ok=False, evidence_id=evidence.id, failures=failures)

        loc = evidence.location
        span_matched = False
        if loc.char_start is not None and loc.char_end is not None:
            if loc.char_start < 0 or loc.char_end > len(normalized_text) or loc.char_start >= loc.char_end:
                failures.append("location_out_of_bounds")
            else:
                span = normalized_text[loc.char_start : loc.char_end]
                if span == evidence.quote:
                    span_matched = True
                else:
                    failures.append("quote_does_not_match_location_span")

        substring_matched = False
        if evidence.quote in normalized_text:
            substring_matched = True
        elif segment_text is not None and evidence.quote in segment_text:
            substring_matched = True

        if not span_matched and not substring_matched:
            if "quote_does_not_match_location_span" not in failures:
                failures.append("quote_not_found_in_archived_text")

        ok = (not failures) and (span_matched or substring_matched)
        return QuoteCheckResult(ok=ok, evidence_id=evidence.id, failures=failures)

    async def check_evidence(self, evidence: Evidence) -> QuoteCheckResult:
        normalized = await self.get_normalized_text(evidence.document_version_id)
        segment_text = None
        if evidence.document_segment_id:
            seg = await self.get_segment(evidence.document_segment_id)
            if seg is not None:
                segment_text = seg.text
        return self.check_quote_against_text(
            evidence, normalized_text=normalized, segment_text=segment_text
        )

    async def verify_claim(
        self,
        claim_id: str,
        *,
        notes: str = "",
        apply: bool = True,
    ) -> VerificationDecision:
        claim = await self.claims.get(claim_id)
        if claim is None:
            raise LookupError(f"claim not found: {claim_id}")
        pairs = await self.evidence.list_for_claim(claim_id)
        if not pairs:
            decision = VerificationDecision(
                claim_id=claim_id,
                decision=VerificationDecisionKind.REJECT,
                notes=notes or "no evidence attached",
                evidence_ids_checked=[],
                quote_check_passed=False,
                failures=["no_evidence"],
            )
            if apply and claim.status == ClaimStatus.PROPOSED:
                await self.claims.transition(
                    claim_id, ClaimStatus.REJECTED, notes=decision.notes
                )
            return decision

        all_failures: list[str] = []
        checked: list[str] = []
        all_ok = True
        for evidence, _link in pairs:
            checked.append(evidence.id)
            result = await self.check_evidence(evidence)
            if not result.ok:
                all_ok = False
                all_failures.extend(f"{evidence.id}:{f}" for f in result.failures)

        if all_ok:
            decision = VerificationDecision(
                claim_id=claim_id,
                decision=VerificationDecisionKind.APPROVE,
                notes=notes,
                evidence_ids_checked=checked,
                quote_check_passed=True,
                failures=[],
            )
            if apply and claim.status == ClaimStatus.PROPOSED:
                await self.claims.transition(
                    claim_id, ClaimStatus.APPROVED, notes=notes or "verified"
                )
        else:
            decision = VerificationDecision(
                claim_id=claim_id,
                decision=VerificationDecisionKind.REJECT,
                notes=notes or "quote verification failed",
                evidence_ids_checked=checked,
                quote_check_passed=False,
                failures=all_failures,
            )
            if apply and claim.status == ClaimStatus.PROPOSED:
                await self.claims.transition(
                    claim_id, ClaimStatus.REJECTED, notes=decision.notes
                )
        return decision

    async def approve_claim(self, claim_id: str, *, notes: str = "") -> VerificationDecision:
        """Approve only after deterministic quote checks succeed."""
        decision = await self.verify_claim(claim_id, notes=notes, apply=False)
        if decision.decision != VerificationDecisionKind.APPROVE:
            # Still reject when applying a failed approve attempt.
            claim = await self.claims.get(claim_id)
            if claim and claim.status == ClaimStatus.PROPOSED:
                await self.claims.transition(
                    claim_id, ClaimStatus.REJECTED, notes=decision.notes
                )
            return decision.model_copy(
                update={"notes": notes or decision.notes or "approve blocked by quote check"}
            )
        await self.claims.transition(claim_id, ClaimStatus.APPROVED, notes=notes or "approved")
        return decision.model_copy(update={"notes": notes or "approved"})

    async def reject_claim(self, claim_id: str, *, notes: str = "") -> VerificationDecision:
        claim = await self.claims.get(claim_id)
        if claim is None:
            raise LookupError(f"claim not found: {claim_id}")
        if claim.status == ClaimStatus.PROPOSED:
            await self.claims.transition(claim_id, ClaimStatus.REJECTED, notes=notes)
        return VerificationDecision(
            claim_id=claim_id,
            decision=VerificationDecisionKind.REJECT,
            notes=notes,
            quote_check_passed=False,
        )
