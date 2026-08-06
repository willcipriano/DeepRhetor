"""In-process application state for the local web UI."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deeprhetor.services.project_store import OpenProject, create_project_async, open_project_async

_SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_-]+")


def slugify(name: str) -> str:
    base = Path(name).stem
    cleaned = _SAFE_SLUG.sub("-", base).strip("-").lower()
    return cleaned or "project"


@dataclass
class ListedProject:
    key: str
    path: Path
    title: str
    prompt: str
    project_id: str | None = None


@dataclass
class AppState:
    """Shared mutable state for one local server process."""

    projects_dir: Path
    opened: dict[str, OpenProject] = field(default_factory=dict)
    active_run_id: str | None = None
    active_project_key: str | None = None
    background_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    _run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def ensure_projects_dir(self) -> Path:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        return self.projects_dir

    def list_project_files(self) -> list[Path]:
        self.ensure_projects_dir()
        files = sorted(
            [
                p
                for p in self.projects_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".deeprhetor", ".sqlite"}
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files

    def path_for_key(self, key: str) -> Path | None:
        for path in self.list_project_files():
            if slugify(path.name) == key or path.stem == key:
                return path
        # Also allow exact stem match against opened keys.
        opened = self.opened.get(key)
        if opened is not None:
            return opened.path
        return None

    def unique_key(self, title: str) -> str:
        base = slugify(title)
        existing = {slugify(p.name) for p in self.list_project_files()} | set(self.opened)
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"

    async def open(self, key: str, *, recover: bool = True) -> OpenProject:
        if key in self.opened:
            return self.opened[key]
        path = self.path_for_key(key)
        if path is None:
            raise FileNotFoundError(f"project not found: {key}")
        opened = await open_project_async(path, recover=recover)
        self.opened[key] = opened
        return opened

    async def create(
        self,
        *,
        title: str,
        prompt: str,
        config_snapshot: dict[str, Any] | None = None,
    ) -> tuple[str, OpenProject]:
        self.ensure_projects_dir()
        key = self.unique_key(title)
        path = self.projects_dir / f"{key}.deeprhetor"
        opened = await create_project_async(
            path,
            title=title,
            prompt=prompt,
            config_snapshot=config_snapshot or {},
        )
        self.opened[key] = opened
        return key, opened

    async def close(self, key: str) -> None:
        opened = self.opened.pop(key, None)
        if opened is not None:
            await opened.dispose()

    def has_active_run(self) -> bool:
        task = self.background_tasks.get(self.active_run_id or "")
        return bool(self.active_run_id and task is not None and not task.done())

    async def claim_run(self, project_key: str, run_id: str) -> None:
        async with self._run_lock:
            if self.has_active_run() and self.active_run_id != run_id:
                raise RuntimeError(
                    f"another research run is active ({self.active_run_id}); "
                    "only one run at a time"
                )
            self.active_run_id = run_id
            self.active_project_key = project_key

    def release_run(self, run_id: str) -> None:
        if self.active_run_id == run_id:
            self.active_run_id = None
            self.active_project_key = None
        self.background_tasks.pop(run_id, None)

    def register_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self.background_tasks[run_id] = task

        def _done(t: asyncio.Task[Any]) -> None:
            self.release_run(run_id)

        task.add_done_callback(_done)

    async def list_projects(self) -> list[ListedProject]:
        items: list[ListedProject] = []
        for path in self.list_project_files():
            key = slugify(path.name)
            title = path.stem
            prompt = ""
            project_id = None
            if key in self.opened:
                project = self.opened[key].project
                title = project.title
                prompt = project.prompt
                project_id = project.id
            else:
                # Lightweight open without recovery for listing metadata.
                try:
                    opened = await open_project_async(path, recover=False)
                    title = opened.project.title
                    prompt = opened.project.prompt
                    project_id = opened.project.id
                    await opened.dispose()
                except Exception:
                    pass
            items.append(
                ListedProject(
                    key=key,
                    path=path,
                    title=title,
                    prompt=prompt,
                    project_id=project_id,
                )
            )
        return items

    def interrupted_runs(self, opened: OpenProject) -> list[str]:
        report = opened.recovery
        if report is None:
            return []
        return list(report.interrupted_run_ids)
