"""Outline, draft, citation-key, and validation-result persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import ValidationOutcome
from deeprhetor.domain.publication import ValidationResult
from deeprhetor.domain.writing import (
    BibEntry,
    CitationKey,
    MarkdownDraft,
    Outline,
    StructuredDraft,
)

from .base import BaseRepository, dumps_json, iso_now, loads_json, parse_dt, utcnow


class StoredOutline(BaseModel):
    id: str
    project_id: str
    plan_id: str | None = None
    title: str
    outline: Outline
    created_at: datetime


class StoredDraft(BaseModel):
    id: str
    project_id: str
    outline_id: str | None = None
    title: str
    status: str = "draft"
    draft: StructuredDraft | None = None
    markdown_draft: MarkdownDraft | None = None
    created_at: datetime
    updated_at: datetime


class OutlineRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        outline: Outline,
        *,
        project_id: str,
        plan_id: str | None = None,
        outline_id: str | None = None,
    ) -> StoredOutline:
        oid = outline_id or outline.id or str(uuid4())
        stored = outline.model_copy(update={"id": oid, "plan_id": outline.plan_id or plan_id or ""})
        now = utcnow()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO outline (id, project_id, plan_id, title, outline_json, created_at) "
                    "VALUES (:id, :project_id, :plan_id, :title, :outline_json, :created_at)"
                ),
                {
                    "id": oid,
                    "project_id": project_id,
                    "plan_id": plan_id or outline.plan_id or None,
                    "title": outline.title,
                    "outline_json": dumps_json(stored.model_dump(mode="json")),
                    "created_at": iso_now(),
                },
            )
        return StoredOutline(
            id=oid,
            project_id=project_id,
            plan_id=plan_id or outline.plan_id,
            title=outline.title,
            outline=stored,
            created_at=now,
        )

    async def get(self, outline_id: str) -> StoredOutline | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, plan_id, title, outline_json, created_at "
                    "FROM outline WHERE id = :id"
                ),
                {"id": outline_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        payload = loads_json(row["outline_json"], default={})
        outline = Outline.model_validate(payload) if payload else Outline(
            id=row["id"], plan_id=row["plan_id"] or "", plan_version=1, title=row["title"]
        )
        return StoredOutline(
            id=row["id"],
            project_id=row["project_id"],
            plan_id=row["plan_id"],
            title=row["title"],
            outline=outline.model_copy(update={"id": row["id"]}),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    async def latest_for_project(self, project_id: str) -> StoredOutline | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, plan_id, title, outline_json, created_at "
                    "FROM outline WHERE project_id = :project_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"project_id": project_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        payload = loads_json(row["outline_json"], default={})
        outline = Outline.model_validate(payload)
        return StoredOutline(
            id=row["id"],
            project_id=row["project_id"],
            plan_id=row["plan_id"],
            title=row["title"],
            outline=outline.model_copy(update={"id": row["id"]}),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )


class DraftRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(
        self,
        draft: StructuredDraft | MarkdownDraft,
        *,
        project_id: str,
        outline_id: str | None = None,
        status: str = "draft",
        draft_id: str | None = None,
    ) -> StoredDraft:
        did = draft_id or draft.id or str(uuid4())
        oid = outline_id or draft.outline_id
        stored = draft.model_copy(update={"id": did, "outline_id": oid})
        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        is_markdown = isinstance(stored, MarkdownDraft) or getattr(stored, "format", None) == "markdown"
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO draft (id, project_id, outline_id, title, status, "
                    "draft_json, created_at, updated_at) "
                    "VALUES (:id, :project_id, :outline_id, :title, :status, "
                    ":draft_json, :created_at, :updated_at)"
                ),
                {
                    "id": did,
                    "project_id": project_id,
                    "outline_id": oid,
                    "title": draft.title,
                    "status": "markdown" if is_markdown else status,
                    "draft_json": dumps_json(stored.model_dump(mode="json")),
                    "created_at": iso,
                    "updated_at": iso,
                },
            )
            if isinstance(stored, StructuredDraft):
                for section in stored.sections:
                    await conn.execute(
                        text(
                            "INSERT INTO draft_section "
                            "(id, draft_id, section_id, title, prose, sort_order, section_json) "
                            "VALUES (:id, :draft_id, :section_id, :title, :prose, :sort_order, "
                            ":section_json)"
                        ),
                        {
                            "id": str(uuid4()),
                            "draft_id": did,
                            "section_id": section.section_id,
                            "title": section.title,
                            "prose": section.prose,
                            "sort_order": section.order,
                            "section_json": dumps_json(section.model_dump(mode="json")),
                        },
                    )
        return StoredDraft(
            id=did,
            project_id=project_id,
            outline_id=oid,
            title=draft.title,
            status="markdown" if is_markdown else status,
            draft=stored if isinstance(stored, StructuredDraft) else None,
            markdown_draft=stored if isinstance(stored, MarkdownDraft) else None,
            created_at=now,
            updated_at=now,
        )

    async def get(self, draft_id: str) -> StoredDraft | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, outline_id, title, status, draft_json, "
                    "created_at, updated_at FROM draft WHERE id = :id"
                ),
                {"id": draft_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return self._from_row(row)

    async def latest_for_project(self, project_id: str) -> StoredDraft | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, outline_id, title, status, draft_json, "
                    "created_at, updated_at FROM draft WHERE project_id = :project_id "
                    "ORDER BY updated_at DESC LIMIT 1"
                ),
                {"project_id": project_id},
            )
            row = result.mappings().first()
        return self._from_row(row) if row else None

    def _from_row(self, row: Any) -> StoredDraft:
        payload = loads_json(row["draft_json"], default={})
        is_markdown = (
            isinstance(payload, dict)
            and (payload.get("format") == "markdown" or "markdown" in payload)
            and "sections" not in payload
        ) or (isinstance(payload, dict) and payload.get("format") == "markdown")
        structured = None
        markdown_draft = None
        if is_markdown:
            markdown_draft = MarkdownDraft.model_validate(payload).model_copy(
                update={"id": row["id"]}
            )
        else:
            structured = StructuredDraft.model_validate(payload).model_copy(
                update={"id": row["id"]}
            )
        return StoredDraft(
            id=row["id"],
            project_id=row["project_id"],
            outline_id=row["outline_id"],
            title=row["title"],
            status=row["status"],
            draft=structured,
            markdown_draft=markdown_draft,
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )


class CitationKeyRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def upsert(self, citation: CitationKey) -> CitationKey:
        cid = citation.id or str(uuid4())
        bib = citation.bib
        if not bib.key:
            bib = bib.model_copy(update={"key": citation.key})
        stored = citation.model_copy(update={"id": cid, "bib": bib})
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO citation_key "
                    "(id, project_id, key, claim_id, evidence_id, document_id, bib_json) "
                    "VALUES (:id, :project_id, :key, :claim_id, :evidence_id, :document_id, "
                    ":bib_json) "
                    "ON CONFLICT(project_id, key) DO UPDATE SET "
                    "claim_id = excluded.claim_id, "
                    "evidence_id = excluded.evidence_id, "
                    "document_id = excluded.document_id, "
                    "bib_json = excluded.bib_json"
                ),
                {
                    "id": cid,
                    "project_id": stored.project_id,
                    "key": stored.key,
                    "claim_id": stored.claim_id,
                    "evidence_id": stored.evidence_id,
                    "document_id": stored.document_id,
                    "bib_json": dumps_json(
                        {
                            **bib.model_dump(mode="json"),
                            "document_version_id": stored.document_version_id,
                            "document_content_sha256": stored.document_content_sha256,
                        }
                    ),
                },
            )
        return stored

    async def get_by_key(self, project_id: str, key: str) -> CitationKey | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, key, claim_id, evidence_id, document_id, bib_json "
                    "FROM citation_key WHERE project_id = :project_id AND key = :key"
                ),
                {"project_id": project_id, "key": key},
            )
            row = result.mappings().first()
        return self._from_row(row) if row else None

    async def list_for_project(self, project_id: str) -> list[CitationKey]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, key, claim_id, evidence_id, document_id, bib_json "
                    "FROM citation_key WHERE project_id = :project_id ORDER BY key"
                ),
                {"project_id": project_id},
            )
            rows = result.mappings().all()
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: Any) -> CitationKey:
        payload = loads_json(row["bib_json"], default={})
        version_id = payload.pop("document_version_id", None)
        content_sha = payload.pop("document_content_sha256", None)
        if "key" not in payload:
            payload["key"] = row["key"]
        bib = BibEntry.model_validate(payload) if payload else BibEntry(key=row["key"])
        return CitationKey(
            id=row["id"],
            project_id=row["project_id"],
            key=row["key"],
            claim_id=row["claim_id"],
            evidence_id=row["evidence_id"],
            document_id=row["document_id"],
            document_version_id=version_id,
            document_content_sha256=content_sha,
            bib=bib,
        )


class ValidationResultRepository(BaseRepository):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def create(self, result: ValidationResult) -> ValidationResult:
        rid = result.id or str(uuid4())
        stored = result.model_copy(update={"id": rid})
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO validation_result "
                    "(id, draft_id, outcome, result_json, created_at) "
                    "VALUES (:id, :draft_id, :outcome, :result_json, :created_at)"
                ),
                {
                    "id": rid,
                    "draft_id": stored.draft_id,
                    "outcome": str(stored.outcome),
                    "result_json": dumps_json(stored.model_dump(mode="json")),
                    "created_at": iso_now(),
                },
            )
        return stored

    async def get(self, validation_id: str) -> ValidationResult | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, draft_id, outcome, result_json, created_at "
                    "FROM validation_result WHERE id = :id"
                ),
                {"id": validation_id},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return self._from_row(row)

    async def latest_for_draft(self, draft_id: str) -> ValidationResult | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, draft_id, outcome, result_json, created_at "
                    "FROM validation_result WHERE draft_id = :draft_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"draft_id": draft_id},
            )
            row = result.mappings().first()
        return self._from_row(row) if row else None

    def _from_row(self, row: Any) -> ValidationResult:
        payload = loads_json(row["result_json"], default={})
        vr = ValidationResult.model_validate(payload)
        return vr.model_copy(
            update={
                "id": row["id"],
                "draft_id": row["draft_id"],
                "outcome": ValidationOutcome(row["outcome"]),
            }
        )
