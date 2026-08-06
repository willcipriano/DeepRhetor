"""Claim and evidence repositories with enforced state transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ClaimStatus, EvidenceDirectness, EvidenceRelation
from deeprhetor.domain.knowledge import (
    ClaimEvidenceLink,
    ClaimRelation,
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from .base import BaseRepository, dumps_json, loads_json, parse_dt, utcnow

# Explicit claim lifecycle. Terminal: rejected, superseded.
CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.PROPOSED: frozenset(
        {
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
            ClaimStatus.SUPERSEDED,
            ClaimStatus.NEEDS_CORRECTION,
        }
    ),
    ClaimStatus.NEEDS_CORRECTION: frozenset(
        {
            ClaimStatus.PROPOSED,
            ClaimStatus.REJECTED,
            ClaimStatus.SUPERSEDED,
        }
    ),
    ClaimStatus.APPROVED: frozenset({ClaimStatus.SUPERSEDED}),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.SUPERSEDED: frozenset(),
}


class ClaimTransitionError(ValueError):
    """Raised when a claim status change is not allowed."""


class StoredClaim(BaseModel):
    id: str
    project_id: str
    run_id: str | None = None
    topic_id: str | None = None
    statement: str
    status: ClaimStatus = ClaimStatus.PROPOSED
    claim: ProposedClaim
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimRepository(BaseRepository):
    """Persist claims and enforce lifecycle transitions."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        claim: ProposedClaim,
        *,
        project_id: str,
        run_id: str | None = None,
        index_fts: bool = True,
    ) -> StoredClaim:
        cid = claim.id or str(uuid4())
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        status = claim.status or ClaimStatus.PROPOSED
        if status not in {ClaimStatus.PROPOSED, ClaimStatus.NEEDS_CORRECTION}:
            raise ClaimTransitionError(
                f"new claims must start as proposed/needs_correction, got {status}"
            )
        stored_model = claim.model_copy(
            update={
                "id": cid,
                "project_id": project_id,
                "run_id": run_id or claim.run_id,
                "status": status,
            }
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO claim "
                    "(id, project_id, run_id, topic_id, statement, status, claim_json, "
                    "created_at, updated_at) "
                    "VALUES (:id, :project_id, :run_id, :topic_id, :statement, :status, "
                    ":claim_json, :created_at, :updated_at)"
                ),
                {
                    "id": cid,
                    "project_id": project_id,
                    "run_id": run_id or claim.run_id,
                    "topic_id": claim.topic_id,
                    "statement": claim.statement,
                    "status": str(status),
                    "claim_json": dumps_json(stored_model.model_dump(mode="json")),
                    "created_at": iso,
                    "updated_at": iso,
                },
            )
        if index_fts:
            from deeprhetor.services.fts import FtsService

            await FtsService(self._engine).index_claim(cid, claim.statement)
        return StoredClaim(
            id=cid,
            project_id=project_id,
            run_id=run_id or claim.run_id,
            topic_id=claim.topic_id,
            statement=claim.statement,
            status=status,
            claim=stored_model,
            created_at=now,
            updated_at=now,
        )

    async def get(self, claim_id: str) -> StoredClaim | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, topic_id, statement, status, "
                    "claim_json, created_at, updated_at FROM claim WHERE id = :id"
                ),
                {"id": claim_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return self._from_row(row)

    async def list_for_project(
        self,
        project_id: str,
        *,
        status: ClaimStatus | None = None,
        run_id: str | None = None,
    ) -> list[StoredClaim]:
        clauses = ["project_id = :project_id"]
        params: dict[str, Any] = {"project_id": project_id}
        if status is not None:
            clauses.append("status = :status")
            params["status"] = str(status)
        if run_id is not None:
            clauses.append("run_id = :run_id")
            params["run_id"] = run_id
        sql = (
            "SELECT id, project_id, run_id, topic_id, statement, status, "
            "claim_json, created_at, updated_at FROM claim WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at"
        )
        async with self.connection() as conn:
            result = await conn.execute(text(sql), params)
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    async def list_by_status(
        self, project_id: str, statuses: Iterable[ClaimStatus]
    ) -> list[StoredClaim]:
        status_list = list(statuses)
        if not status_list:
            return []
        placeholders = ", ".join(f":s{i}" for i in range(len(status_list)))
        params: dict[str, Any] = {"project_id": project_id}
        for i, st in enumerate(status_list):
            params[f"s{i}"] = str(st)
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, topic_id, statement, status, "
                    "claim_json, created_at, updated_at FROM claim "
                    f"WHERE project_id = :project_id AND status IN ({placeholders}) "
                    "ORDER BY created_at"
                ),
                params,
            )
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    async def transition(
        self,
        claim_id: str,
        new_status: ClaimStatus,
        *,
        notes: str | None = None,
        corrected_statement: str | None = None,
    ) -> StoredClaim:
        current = await self.get(claim_id)
        if current is None:
            raise LookupError(f"claim not found: {claim_id}")
        allowed = CLAIM_TRANSITIONS.get(current.status, frozenset())
        if new_status not in allowed:
            raise ClaimTransitionError(
                f"cannot transition claim {claim_id} from {current.status} to {new_status}"
            )
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        updates: dict[str, Any] = {"status": new_status}
        if corrected_statement is not None:
            updates["statement"] = corrected_statement
        meta = dict(current.claim.metadata)
        if notes:
            meta.setdefault("transition_notes", []).append(
                {"to": str(new_status), "notes": notes, "at": iso}
            )
        updated_claim = current.claim.model_copy(
            update={
                "status": new_status,
                "statement": corrected_statement or current.statement,
                "metadata": meta,
            }
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE claim SET status = :status, statement = :statement, "
                    "claim_json = :claim_json, updated_at = :updated_at WHERE id = :id"
                ),
                {
                    "id": claim_id,
                    "status": str(new_status),
                    "statement": updated_claim.statement,
                    "claim_json": dumps_json(updated_claim.model_dump(mode="json")),
                    "updated_at": iso,
                },
            )
        if corrected_statement is not None:
            from deeprhetor.services.fts import FtsService

            await FtsService(self._engine).index_claim(claim_id, corrected_statement)
        stored = await self.get(claim_id)
        assert stored is not None
        return stored

    async def attach_evidence(
        self,
        claim_id: str,
        evidence_id: str,
        *,
        relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
        directness: EvidenceDirectness = EvidenceDirectness.DIRECT,
        explanation: str = "",
    ) -> ClaimEvidenceLink:
        claim = await self.get(claim_id)
        if claim is None:
            raise LookupError(f"claim not found: {claim_id}")
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO claim_evidence "
                    "(claim_id, evidence_id, relation, directness, explanation) "
                    "VALUES (:claim_id, :evidence_id, :relation, :directness, :explanation) "
                    "ON CONFLICT(claim_id, evidence_id) DO UPDATE SET "
                    "relation = excluded.relation, directness = excluded.directness, "
                    "explanation = excluded.explanation"
                ),
                {
                    "claim_id": claim_id,
                    "evidence_id": evidence_id,
                    "relation": str(relation),
                    "directness": str(directness),
                    "explanation": explanation,
                },
            )
        link = ClaimEvidenceLink(
            evidence_id=evidence_id,
            relation=relation,
            directness=directness,
            explanation=explanation,
        )
        # Keep claim_json evidence_links in sync.
        links = [lnk for lnk in claim.claim.evidence_links if lnk.evidence_id != evidence_id]
        links.append(link)
        updated = claim.claim.model_copy(update={"evidence_links": links})
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE claim SET claim_json = :claim_json, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {
                    "id": claim_id,
                    "claim_json": dumps_json(updated.model_dump(mode="json")),
                    "updated_at": utcnow().replace(microsecond=0).isoformat(),
                },
            )
        return link

    async def record_relation(self, relation: ClaimRelation) -> ClaimRelation:
        rid = str(uuid4())
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO claim_relation "
                    "(id, from_claim_id, to_claim_id, relation, notes) "
                    "VALUES (:id, :from_claim_id, :to_claim_id, :relation, :notes)"
                ),
                {
                    "id": rid,
                    "from_claim_id": relation.from_claim_id,
                    "to_claim_id": relation.to_claim_id,
                    "relation": relation.relation,
                    "notes": relation.notes,
                },
            )
        return relation

    def _from_row(self, row: Any) -> StoredClaim:
        payload = loads_json(row["claim_json"], default={})
        try:
            claim = ProposedClaim.model_validate(payload) if payload else ProposedClaim(
                id=row["id"],
                statement=row["statement"],
                status=ClaimStatus(row["status"]),
                project_id=row["project_id"],
                run_id=row["run_id"],
                topic_id=row["topic_id"],
            )
        except Exception:
            claim = ProposedClaim(
                id=row["id"],
                statement=row["statement"],
                status=ClaimStatus(row["status"]),
                project_id=row["project_id"],
                run_id=row["run_id"],
                topic_id=row["topic_id"],
            )
        claim = claim.model_copy(
            update={
                "id": row["id"],
                "statement": row["statement"],
                "status": ClaimStatus(row["status"]),
                "project_id": row["project_id"],
                "run_id": row["run_id"],
                "topic_id": row["topic_id"],
            }
        )
        return StoredClaim(
            id=row["id"],
            project_id=row["project_id"],
            run_id=row["run_id"],
            topic_id=row["topic_id"],
            statement=row["statement"],
            status=ClaimStatus(row["status"]),
            claim=claim,
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )


class EvidenceRepository(BaseRepository):
    """Persist evidence spans and claim links."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(self, evidence: Evidence) -> Evidence:
        eid = evidence.id or str(uuid4())
        ensured = evidence.ensure_content_hash().model_copy(update={"id": eid})
        if not ensured.quote:
            raise ValueError("evidence quote must be non-empty")
        if ensured.content_hash != quote_content_hash(ensured.quote):
            raise ValueError("evidence content_hash does not match quote")
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO evidence "
                    "(id, document_id, document_version_id, document_segment_id, quote, "
                    "location_json, content_hash, created_at) "
                    "VALUES (:id, :document_id, :document_version_id, :document_segment_id, "
                    ":quote, :location_json, :content_hash, :created_at)"
                ),
                {
                    "id": eid,
                    "document_id": ensured.document_id,
                    "document_version_id": ensured.document_version_id,
                    "document_segment_id": ensured.document_segment_id,
                    "quote": ensured.quote,
                    "location_json": dumps_json(ensured.location.model_dump(mode="json")),
                    "content_hash": ensured.content_hash,
                    "created_at": iso,
                },
            )
        return ensured.model_copy(update={"created_at": now})

    async def get(self, evidence_id: str) -> Evidence | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, document_id, document_version_id, document_segment_id, "
                    "quote, location_json, content_hash, created_at FROM evidence "
                    "WHERE id = :id"
                ),
                {"id": evidence_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        loc = loads_json(row["location_json"], default={})
        return Evidence(
            id=row["id"],
            document_id=row["document_id"],
            document_version_id=row["document_version_id"],
            document_segment_id=row["document_segment_id"],
            quote=row["quote"],
            location=EvidenceLocation.model_validate(loc) if loc else EvidenceLocation(),
            content_hash=row["content_hash"],
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    async def list_for_claim(self, claim_id: str) -> list[tuple[Evidence, ClaimEvidenceLink]]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT e.id, e.document_id, e.document_version_id, e.document_segment_id, "
                    "e.quote, e.location_json, e.content_hash, e.created_at, "
                    "ce.relation, ce.directness, ce.explanation "
                    "FROM evidence e "
                    "JOIN claim_evidence ce ON ce.evidence_id = e.id "
                    "WHERE ce.claim_id = :claim_id"
                ),
                {"claim_id": claim_id},
            )
            rows = result.mappings().all()
        out: list[tuple[Evidence, ClaimEvidenceLink]] = []
        for row in rows:
            loc = loads_json(row["location_json"], default={})
            evidence = Evidence(
                id=row["id"],
                document_id=row["document_id"],
                document_version_id=row["document_version_id"],
                document_segment_id=row["document_segment_id"],
                quote=row["quote"],
                location=EvidenceLocation.model_validate(loc) if loc else EvidenceLocation(),
                content_hash=row["content_hash"],
                created_at=parse_dt(row["created_at"]) or utcnow(),
            )
            link = ClaimEvidenceLink(
                evidence_id=row["id"],
                relation=EvidenceRelation(row["relation"]),
                directness=EvidenceDirectness(row["directness"]),
                explanation=row["explanation"] or "",
            )
            out.append((evidence, link))
        return out
