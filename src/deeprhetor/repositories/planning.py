"""Research plan persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import PlanStatus, RhetoricalPosture
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan

from .base import BaseRepository, dumps_json, iso_now, loads_json, parse_dt, utcnow


class StoredResearchPlan(BaseModel):
    """Row shape for ``research_plan`` plus reconstructed domain model."""

    id: str
    project_id: str
    run_id: str | None = None
    version: int = 1
    status: PlanStatus = PlanStatus.DRAFT
    rhetorical_posture: RhetoricalPosture | None = None
    plan: ResearchPlan
    created_at: datetime
    approved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchPlanRepository(BaseRepository):
    """Create, load, and approve research_plan rows (with topic/section children)."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)

    async def next_version(self, project_id: str) -> int:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT COALESCE(MAX(version), 0) FROM research_plan "
                    "WHERE project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            current = int(result.scalar_one() or 0)
        return current + 1

    async def create(
        self,
        plan: ResearchPlan,
        *,
        run_id: str | None = None,
        plan_id: str | None = None,
        supersede_previous: bool = True,
    ) -> StoredResearchPlan:
        """Persist a plan version. Defaults version from existing rows when unset."""
        pid = plan_id or plan.id or str(uuid4())
        version = plan.version
        if version <= 0:
            version = await self.next_version(plan.project_id)
        else:
            # If the caller reuses version 1 on a fresh project this is fine;
            # collisions with UNIQUE(project_id, version) raise from SQLite.
            existing = await self.get_by_project_version(plan.project_id, version)
            if existing is not None:
                version = await self.next_version(plan.project_id)

        now = utcnow()
        iso = now.replace(microsecond=0).isoformat()
        stored_plan = plan.model_copy(
            update={"id": pid, "version": version, "status": plan.status}
        )
        plan_json = stored_plan.model_dump(mode="json")

        async with self._engine.begin() as conn:
            if supersede_previous:
                await conn.execute(
                    text(
                        "UPDATE research_plan SET status = :superseded "
                        "WHERE project_id = :project_id AND status IN (:draft, :awaiting) "
                        "AND version < :version"
                    ),
                    {
                        "superseded": str(PlanStatus.SUPERSEDED),
                        "project_id": plan.project_id,
                        "draft": str(PlanStatus.DRAFT),
                        "awaiting": str(PlanStatus.AWAITING_APPROVAL),
                        "version": version,
                    },
                )
            await conn.execute(
                text(
                    "INSERT INTO research_plan "
                    "(id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at) "
                    "VALUES (:id, :project_id, :run_id, :version, :status, "
                    ":rhetorical_posture, :plan_json, :created_at, NULL)"
                ),
                {
                    "id": pid,
                    "project_id": plan.project_id,
                    "run_id": run_id,
                    "version": version,
                    "status": str(stored_plan.status),
                    "rhetorical_posture": str(stored_plan.rhetorical_posture)
                    if stored_plan.rhetorical_posture
                    else None,
                    "plan_json": dumps_json(plan_json),
                    "created_at": iso,
                },
            )
            for order, topic in enumerate(stored_plan.topics):
                # Row PK is global; keep stable topic_id inside topic_json only.
                topic_row_id = str(uuid4())
                await conn.execute(
                    text(
                        "INSERT INTO plan_topic "
                        "(id, plan_id, title, objective, topic_json, sort_order) "
                        "VALUES (:id, :plan_id, :title, :objective, :topic_json, :sort_order)"
                    ),
                    {
                        "id": topic_row_id,
                        "plan_id": pid,
                        "title": topic.title,
                        "objective": topic.objective,
                        "topic_json": dumps_json(topic.model_dump(mode="json")),
                        "sort_order": order,
                    },
                )
            for order, section in enumerate(stored_plan.sections):
                section_row_id = str(uuid4())
                await conn.execute(
                    text(
                        "INSERT INTO plan_section "
                        "(id, plan_id, title, section_json, sort_order) "
                        "VALUES (:id, :plan_id, :title, :section_json, :sort_order)"
                    ),
                    {
                        "id": section_row_id,
                        "plan_id": pid,
                        "title": section.title,
                        "section_json": dumps_json(section.model_dump(mode="json")),
                        "sort_order": section.order if section.order else order,
                    },
                )

        return StoredResearchPlan(
            id=pid,
            project_id=plan.project_id,
            run_id=run_id,
            version=version,
            status=stored_plan.status,
            rhetorical_posture=stored_plan.rhetorical_posture,
            plan=stored_plan,
            created_at=now,
        )

    async def get(self, plan_id: str) -> StoredResearchPlan | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at "
                    "FROM research_plan WHERE id = :id"
                ),
                {"id": plan_id},
            )
            row = result.mappings().first()
        return _stored_from_row(row) if row else None

    async def get_by_project_version(
        self, project_id: str, version: int
    ) -> StoredResearchPlan | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at "
                    "FROM research_plan WHERE project_id = :project_id AND version = :version"
                ),
                {"project_id": project_id, "version": version},
            )
            row = result.mappings().first()
        return _stored_from_row(row) if row else None

    async def latest_for_project(self, project_id: str) -> StoredResearchPlan | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at "
                    "FROM research_plan WHERE project_id = :project_id "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"project_id": project_id},
            )
            row = result.mappings().first()
        return _stored_from_row(row) if row else None

    async def latest_for_run(self, run_id: str) -> StoredResearchPlan | None:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at "
                    "FROM research_plan WHERE run_id = :run_id "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"run_id": run_id},
            )
            row = result.mappings().first()
        return _stored_from_row(row) if row else None

    async def list_for_project(self, project_id: str) -> list[StoredResearchPlan]:
        async with self.connection() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, project_id, run_id, version, status, rhetorical_posture, "
                    "plan_json, created_at, approved_at "
                    "FROM research_plan WHERE project_id = :project_id "
                    "ORDER BY version ASC"
                ),
                {"project_id": project_id},
            )
            rows = result.mappings().all()
        return [_stored_from_row(row) for row in rows]

    async def update_status(
        self,
        plan_id: str,
        status: PlanStatus,
        *,
        approved_at: datetime | None = None,
        plan: ResearchPlan | None = None,
    ) -> StoredResearchPlan | None:
        fields = ["status = :status"]
        params: dict[str, Any] = {"id": plan_id, "status": str(status)}
        if approved_at is not None or status == PlanStatus.APPROVED:
            fields.append("approved_at = :approved_at")
            params["approved_at"] = (
                approved_at or utcnow()
            ).replace(microsecond=0).isoformat()
        if plan is not None:
            fields.append("plan_json = :plan_json")
            fields.append("rhetorical_posture = :rhetorical_posture")
            params["plan_json"] = dumps_json(plan.model_dump(mode="json"))
            params["rhetorical_posture"] = (
                str(plan.rhetorical_posture) if plan.rhetorical_posture else None
            )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE research_plan SET {', '.join(fields)} WHERE id = :id"),
                params,
            )
        return await self.get(plan_id)


def _stored_from_row(row: Any) -> StoredResearchPlan:
    raw = loads_json(row["plan_json"])
    plan = ResearchPlan.model_validate(raw)
    posture = row["rhetorical_posture"]
    return StoredResearchPlan(
        id=row["id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        version=row["version"],
        status=PlanStatus(row["status"]),
        rhetorical_posture=RhetoricalPosture(posture) if posture else None,
        plan=plan,
        created_at=parse_dt(row["created_at"]) or utcnow(),
        approved_at=parse_dt(row["approved_at"]),
    )


__all__ = [
    "PlanSection",
    "PlanTopic",
    "ResearchPlanRepository",
    "StoredResearchPlan",
]
