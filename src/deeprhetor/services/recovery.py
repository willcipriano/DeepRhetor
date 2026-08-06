"""Interrupted-run recovery and explicit resume/retry/abandon transitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import RunStatus, TaskStatus
from deeprhetor.repositories.base import iso_now
from deeprhetor.repositories.workflow import Run, RunRepository, Task, TaskRepository

_ALLOWED_RESUME_FROM = frozenset({RunStatus.INTERRUPTED, RunStatus.FAILED})
_ALLOWED_RETRY_FROM = frozenset({TaskStatus.INTERRUPTED, TaskStatus.FAILED})


@dataclass
class RecoveryReport:
    interrupted_task_ids: list[str] = field(default_factory=list)
    interrupted_run_ids: list[str] = field(default_factory=list)

    @property
    def had_orphans(self) -> bool:
        return bool(self.interrupted_task_ids or self.interrupted_run_ids)


async def mark_orphaned_in_progress(engine: AsyncEngine) -> RecoveryReport:
    """On open/startup: mark orphaned in-progress work as ``interrupted``.

    A single local process owns the project file. Any ``running`` / ``ready``
    (or run ``awaiting_plan_approval``) status without a live owner becomes
    ``interrupted``. Terminal statuses are left alone.
    """
    report = RecoveryReport()
    now = iso_now()
    async with engine.begin() as conn:
        task_result = await conn.execute(
            text(
                "UPDATE task SET status = :new_status, updated_at = :updated_at, "
                "finished_at = COALESCE(finished_at, :finished_at) "
                "WHERE status IN (:s1, :s2) "
                "RETURNING id"
            ),
            {
                "new_status": str(TaskStatus.INTERRUPTED),
                "updated_at": now,
                "finished_at": now,
                "s1": str(TaskStatus.RUNNING),
                "s2": str(TaskStatus.READY),
            },
        )
        report.interrupted_task_ids = [row[0] for row in task_result.fetchall()]

        run_result = await conn.execute(
            text(
                "UPDATE run SET status = :new_status, "
                "finished_at = COALESCE(finished_at, :finished_at) "
                "WHERE status IN (:s1, :s2) "
                "RETURNING id"
            ),
            {
                "new_status": str(RunStatus.INTERRUPTED),
                "finished_at": now,
                "s1": str(RunStatus.RUNNING),
                "s2": str(RunStatus.AWAITING_PLAN_APPROVAL),
            },
        )
        report.interrupted_run_ids = [row[0] for row in run_result.fetchall()]
    return report


class RecoveryService:
    """Explicit recovery APIs with clear state transitions."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._runs = RunRepository(engine)
        self._tasks = TaskRepository(engine)

    async def resume_run(self, run_id: str) -> Run:
        """``interrupted|failed`` → ``running``.

        Interrupted tasks remain ``interrupted`` until ``retry_task`` selects them.
        """
        run = await self._runs.get(run_id)
        if run is None:
            raise LookupError(f"run not found: {run_id}")
        if run.status not in _ALLOWED_RESUME_FROM:
            raise ValueError(
                f"cannot resume run in status {run.status!s}; "
                f"allowed: {sorted(s.value for s in _ALLOWED_RESUME_FROM)}"
            )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE run SET status = :status, finished_at = NULL WHERE id = :id"
                ),
                {"status": str(RunStatus.RUNNING), "id": run_id},
            )
        resumed = await self._runs.get(run_id)
        assert resumed is not None
        return resumed

    async def retry_task(self, task_id: str) -> Task:
        """``interrupted|failed`` → ``pending`` with attempt incremented."""
        task = await self._tasks.get(task_id)
        if task is None:
            raise LookupError(f"task not found: {task_id}")
        if task.status not in _ALLOWED_RETRY_FROM:
            raise ValueError(
                f"cannot retry task in status {task.status!s}; "
                f"allowed: {sorted(s.value for s in _ALLOWED_RETRY_FROM)}"
            )
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE task SET status = :status, attempt = attempt + 1, "
                    "finished_at = NULL, started_at = NULL, error_message = NULL, "
                    "updated_at = :updated_at WHERE id = :id"
                ),
                {
                    "status": str(TaskStatus.PENDING),
                    "updated_at": iso_now(),
                    "id": task_id,
                },
            )
        retried = await self._tasks.get(task_id)
        assert retried is not None
        return retried

    async def abandon_run(self, run_id: str) -> Run:
        """Any non-terminal run → ``abandoned``; open tasks → ``cancelled``."""
        run = await self._runs.get(run_id)
        if run is None:
            raise LookupError(f"run not found: {run_id}")
        if run.status == RunStatus.ABANDONED:
            return run
        if run.status == RunStatus.COMPLETED:
            raise ValueError(f"cannot abandon run in status {run.status!s}")

        now = iso_now()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE task SET status = :new_status, updated_at = :now, "
                    "finished_at = COALESCE(finished_at, :now) "
                    "WHERE run_id = :run_id AND status NOT IN "
                    "('completed', 'failed', 'cancelled')"
                ),
                {
                    "new_status": str(TaskStatus.CANCELLED),
                    "now": now,
                    "run_id": run_id,
                },
            )
            await conn.execute(
                text(
                    "UPDATE run SET status = :status, finished_at = :now WHERE id = :id"
                ),
                {
                    "status": str(RunStatus.ABANDONED),
                    "now": now,
                    "id": run_id,
                },
            )
        abandoned = await self._runs.get(run_id)
        assert abandoned is not None
        return abandoned
