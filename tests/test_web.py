"""Stage 9 FastAPI / Jinja / HTMX web UI tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deeprhetor.cli import build_parser
from deeprhetor.domain.enums import RunStatus, TaskStatus
from deeprhetor.repositories.operations import ArtifactRepository, EventRepository
from deeprhetor.repositories.workflow import RunRepository, TaskRepository
from deeprhetor.services.project_store import create_project_async, open_project_async
from deeprhetor.web import create_app
from deeprhetor.web.state import slugify


@pytest.fixture
def projects_dir(tmp_path: Path) -> Path:
    d = tmp_path / "projects"
    d.mkdir()
    return d


@pytest.fixture
def client(projects_dir: Path):
    app = create_app(projects_dir=projects_dir)
    with TestClient(app) as c:
        yield c


def test_serve_defaults_to_loopback() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_index_and_create_project(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "DeepRhetor" in r.text
    assert "Create project" in r.text

    r = client.post(
        "/projects",
        data={"title": "Demo", "prompt": "Explain rhetoric"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/projects/")
    key = r.headers["location"].rsplit("/", 1)[-1]

    r = client.get(f"/projects/{key}")
    assert r.status_code == 200
    assert "Explain rhetoric" in r.text
    assert "Model preset" in r.text
    assert "Sources" in r.text


def test_settings_plan_corpus_draft_export_routes(client: TestClient) -> None:
    r = client.post(
        "/projects",
        data={"title": "Screens", "prompt": "Research topic"},
        follow_redirects=False,
    )
    key = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(
        f"/projects/{key}/settings",
        data={
            "title": "Screens",
            "prompt": "Research topic updated",
            "model_preset": "cheap",
            "max_search_results_per_assignment": "10",
            "max_follow_up_searches": "2",
            "max_critic_passes": "3",
            "max_run_duration_seconds": "600",
            "source_tavily": "1",
            "source_local": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    for path in (
        f"/projects/{key}/plan",
        f"/projects/{key}/corpus",
        f"/projects/{key}/draft",
        f"/projects/{key}/export",
        f"/projects/{key}/recovery",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path


@pytest.mark.asyncio
async def test_sse_returns_events(projects_dir: Path) -> None:
    path = projects_dir / "sse.deeprhetor"
    opened = await create_project_async(path, title="SSE", prompt="p")
    key = slugify(path.name)
    runs = RunRepository(opened.engine)
    # Completed so the SSE stream terminates after draining events.
    run = await runs.create(project_id=opened.project.id, status=RunStatus.COMPLETED)
    events = EventRepository(opened.engine)
    await events.create(kind="test.ping", message="hello-sse", run_id=run.id)
    await events.create(kind="test.pong", message="again", run_id=run.id)
    await opened.dispose()

    app = create_app(projects_dir=projects_dir)
    with TestClient(app) as client:
        assert client.get(f"/projects/{key}").status_code == 200
        with client.stream("GET", f"/runs/{run.id}/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            chunks: list[str] = []
            for line in response.iter_lines():
                if line:
                    chunks.append(line)
                joined = "\n".join(chunks)
                if "hello-sse" in joined and (
                    "stream.end" in joined or len(chunks) > 60
                ):
                    break
            blob = "\n".join(chunks)
            assert "hello-sse" in blob
            assert "data:" in blob


@pytest.mark.asyncio
async def test_recovery_screen_for_interrupted_runs(projects_dir: Path) -> None:
    path = projects_dir / "recover-ui.deeprhetor"
    opened = await create_project_async(path, title="RecoverUI", prompt="p")
    key = slugify(path.name)
    runs = RunRepository(opened.engine)
    tasks = TaskRepository(opened.engine)
    run = await runs.create(project_id=opened.project.id, status=RunStatus.RUNNING)
    await tasks.create(
        run_id=run.id,
        kind="scan",
        status=TaskStatus.RUNNING,
        idempotency_key="scan-ui-1",
    )
    await opened.dispose()

    reopened = await open_project_async(path)
    assert reopened.recovery is not None
    assert run.id in reopened.recovery.interrupted_run_ids
    await reopened.dispose()

    app = create_app(projects_dir=projects_dir)
    with TestClient(app) as client:
        r = client.get(f"/projects/{key}/recovery")
        assert r.status_code == 200
        assert "Interrupted-run recovery" in r.text
        assert run.id in r.text
        assert "Resume run" in r.text
        assert "Abandon run" in r.text

        r = client.post(
            f"/projects/{key}/recovery/{run.id}/abandon",
            follow_redirects=False,
        )
        assert r.status_code == 303

        opened2 = await open_project_async(path, recover=False)
        updated = await RunRepository(opened2.engine).get(run.id)
        assert updated is not None
        assert updated.status == RunStatus.ABANDONED
        await opened2.dispose()


def test_start_run_and_live_page(client: TestClient) -> None:
    r = client.post(
        "/projects",
        data={"title": "Live", "prompt": "Plan something"},
        follow_redirects=False,
    )
    key = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(f"/projects/{key}/runs", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "/plan" in loc
    assert "run_id=" in loc
    run_id = loc.split("run_id=")[-1]

    plan_ok = False
    for _ in range(50):
        resp = client.get(f"/projects/{key}/plan?run_id={run_id}")
        assert resp.status_code == 200
        if "Topics" in resp.text or "No plan generated yet" in resp.text:
            plan_ok = True
            if "Approve plan" in resp.text or "Version" in resp.text:
                break
        time.sleep(0.1)
    assert plan_ok

    live = client.get(f"/projects/{key}/runs/{run_id}")
    assert live.status_code == 200
    assert "Live run" in live.text
    assert "Event stream" in live.text


def test_artifact_download_link(projects_dir: Path) -> None:
    async def _prepare() -> tuple[str, str]:
        path = projects_dir / "art.deeprhetor"
        opened = await create_project_async(path, title="Art", prompt="p")
        art = await ArtifactRepository(opened.engine).create(
            project_id=opened.project.id,
            kind="tex",
            media_type="application/x-tex",
            path_or_name="report.tex",
            data=b"\\documentclass{article}\\begin{document}Hi\\end{document}",
        )
        key = slugify(path.name)
        art_id = art.id
        await opened.dispose()
        return key, art_id

    import asyncio

    key, art_id = asyncio.run(_prepare())
    app = create_app(projects_dir=projects_dir)
    with TestClient(app) as client:
        r = client.get(f"/projects/{key}/export")
        assert r.status_code == 200
        assert "report.tex" in r.text

        r = client.get(f"/projects/{key}/artifacts/{art_id}")
        assert r.status_code == 200
        assert b"documentclass" in r.content
