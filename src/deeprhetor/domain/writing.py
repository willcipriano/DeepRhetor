"""Outline and structured draft models."""

from __future__ import annotations

from pydantic import Field

from .base import DomainModel, IdentifiedModel


class OutlineSection(DomainModel):
    section_id: str
    title: str
    claim_ids: list[str] = Field(default_factory=list)
    notes: str | None = None
    children: list["OutlineSection"] = Field(default_factory=list)
    order: int = 0


class Outline(IdentifiedModel):
    plan_id: str
    plan_version: int
    title: str
    sections: list[OutlineSection] = Field(default_factory=list)


class DraftSection(DomainModel):
    section_id: str
    title: str
    prose: str
    citation_keys: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    completion_notes: str | None = None
    order: int = 0


class StructuredDraft(IdentifiedModel):
    outline_id: str
    title: str
    abstract: str | None = None
    sections: list[DraftSection] = Field(default_factory=list)
    bibliography_keys: list[str] = Field(default_factory=list)


class BibEntry(DomainModel):
    """Deterministic bibliography metadata for a citation key."""

    key: str
    entry_type: str = "misc"  # article | book | online | misc | …
    title: str = ""
    author: str = ""
    year: str = ""
    url: str | None = None
    doi: str | None = None
    publisher: str | None = None
    note: str | None = None
    howpublished: str | None = None
    urldate: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class CitationKey(IdentifiedModel):
    """Stable citation key bound to claim/evidence/document + bib metadata."""

    project_id: str
    key: str
    claim_id: str | None = None
    evidence_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    document_content_sha256: str | None = None
    bib: BibEntry = Field(default_factory=lambda: BibEntry(key=""))
