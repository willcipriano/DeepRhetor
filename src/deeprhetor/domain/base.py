"""Base helpers for versioned domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class DomainModel(BaseModel):
    """Versioned Pydantic base for structured outputs and repository DTOs."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    schema_version: int = Field(default=1, ge=1)


class IdentifiedModel(DomainModel):
    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
