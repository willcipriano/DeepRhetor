"""Typed repository stubs. No general execute_sql surface for models."""

from __future__ import annotations

from .base import BaseRepository
from .project import Project, ProjectRepository

__all__ = ["BaseRepository", "Project", "ProjectRepository"]
