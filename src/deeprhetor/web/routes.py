"""HTTP routes for the DeepRhetor local product UI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from deeprhetor.config.loader import load_config
from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.enums import RunStatus
from deeprhetor.domain.sources import RawDocument
from deeprhetor.plugins.parsers import guess_media_type
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository
from deeprhetor.repositories.operations import ArtifactRepository, ErrorRepository, EventRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.repositories.project import ProjectRepository
from deeprhetor.repositories.scan import ScanRepository
from deeprhetor.repositories.workflow import RunRepository, TaskRepository
from deeprhetor.repositories.writing import (
    DraftRepository,
    OutlineRepository,
    ValidationResultRepository,
)
from deeprhetor.services.local_import import LocalFileImporter
from deeprhetor.services.recovery import RecoveryService
from deeprhetor.web.state import AppState
from deeprhetor.workflow import (
    FakeSupervisor,
    open_workflow,
    resume_with_approval,
    start_until_plan_interrupt,
)

router = APIRouter()

_ACTIVE_RUN = frozenset(
    {
        RunStatus.CREATED,
        RunStatus.AWAITING_PLAN_APPROVAL,
        RunStatus.RUNNING,
    }
)


def _state(request: Request) -> AppState:
    return request.app.state.app_state


def _templates(request: Request):
    return request.app.state.templates


def _cfg() -> AppConfig:
    try:
        return load_config()
    except Exception:
        return AppConfig()


async def _project_or_404(state: AppState, key: str):
    try:
        return await state.open(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _nav(key: str | None = None, active: str = "") -> dict[str, Any]:
    return {"project_key": key, "nav_active": active}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    state = _state(request)
    projects = await state.list_projects()
    return _templates(request).TemplateResponse(
        request,
        "index.html",
        {
            **_nav(active="home"),
            "projects": projects,
            "active_run_id": state.active_run_id,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/projects")
async def create_project(
    request: Request,
    title: str = Form(...),
    prompt: str = Form(...),
) -> RedirectResponse:
    state = _state(request)
    title = title.strip()
    prompt = prompt.strip()
    if not title or not prompt:
        return RedirectResponse("/?error=Title+and+prompt+are+required", status_code=303)
    cfg = _cfg()
    snapshot = {
        "limits": cfg.limits.model_dump(),
        "models": {k: v.model_dump() for k, v in cfg.models.items()},
        "sources": {
            "tavily": True,
            "openalex": True,
            "crossref": True,
            "arxiv": True,
            "mediawiki": True,
            "local": True,
        },
        "model_preset": "mid",
    }
    key, _opened = await state.create(
        title=title,
        prompt=prompt,
        config_snapshot=snapshot,
    )
    return RedirectResponse(f"/projects/{key}", status_code=303)


@router.post("/projects/open")
async def open_project_path(
    request: Request,
    path: str = Form(...),
) -> RedirectResponse:
    state = _state(request)
    src = Path(path.strip())
    if not src.is_file():
        return RedirectResponse("/?error=Project+file+not+found", status_code=303)
    state.ensure_projects_dir()
    from deeprhetor.web.state import slugify

    key = slugify(src.name)
    dest = state.projects_dir / f"{key}{src.suffix.lower()}"
    if src.resolve() != dest.resolve():
        # Prefer working inside projects_dir; copy if needed.
        if not dest.exists():
            import shutil

            shutil.copy2(src, dest)
        elif src.resolve() != dest.resolve():
            # Same name already present — open existing dest.
            pass
    opened = await state.open(key)
    _ = opened
    return RedirectResponse(f"/projects/{key}", status_code=303)


@router.get("/projects/{key}", response_class=HTMLResponse)
async def project_settings(request: Request, key: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    cfg = _cfg()
    runs = await RunRepository(opened.engine).list_for_project(opened.project.id)
    docs = await DocumentRepository(opened.engine).list_for_project(opened.project.id)
    # Prefer latest snapshot settings from DB if present.
    from sqlalchemy import text

    settings: dict[str, Any] = {}
    model_preset = "mid"
    async with opened.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT settings_json, model_presets_json FROM configuration_snapshot "
                    "WHERE project_id = :pid ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": opened.project.id},
            )
        ).mappings().first()
    if row:
        from deeprhetor.repositories.base import loads_json

        settings = loads_json(row["settings_json"])
        model_preset = settings.get("model_preset", "mid")
    interrupted = [
        r for r in runs if r.status in {RunStatus.INTERRUPTED, RunStatus.FAILED}
    ]
    if opened.recovery and opened.recovery.had_orphans:
        for rid in opened.recovery.interrupted_run_ids:
            if all(r.id != rid for r in interrupted):
                run = await RunRepository(opened.engine).get(rid)
                if run:
                    interrupted.append(run)

    return _templates(request).TemplateResponse(
        request,
        "project.html",
        {
            **_nav(key, "settings"),
            "project": opened.project,
            "path": str(opened.path),
            "documents": docs,
            "runs": runs,
            "settings": settings,
            "limits": settings.get("limits") or cfg.limits.model_dump(),
            "sources": settings.get("sources")
            or {
                "tavily": True,
                "openalex": True,
                "crossref": True,
                "arxiv": True,
                "mediawiki": True,
                "local": True,
            },
            "model_preset": model_preset,
            "model_presets": list(cfg.models.keys()),
            "interrupted_runs": interrupted,
            "active_run_id": state.active_run_id,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/projects/{key}/settings")
async def save_settings(
    request: Request,
    key: str,
    title: str = Form(...),
    prompt: str = Form(...),
    model_preset: str = Form("mid"),
    max_search_results_per_assignment: int = Form(25),
    max_follow_up_searches: int = Form(5),
    max_critic_passes: int = Form(5),
    max_run_duration_seconds: int = Form(7200),
    source_tavily: str | None = Form(None),
    source_openalex: str | None = Form(None),
    source_crossref: str | None = Form(None),
    source_arxiv: str | None = Form(None),
    source_mediawiki: str | None = Form(None),
    source_local: str | None = Form(None),
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    cfg = _cfg()
    sources = {
        "tavily": bool(source_tavily),
        "openalex": bool(source_openalex),
        "crossref": bool(source_crossref),
        "arxiv": bool(source_arxiv),
        "mediawiki": bool(source_mediawiki),
        "local": bool(source_local),
    }
    limits = {
        **cfg.limits.model_dump(),
        "max_search_results_per_assignment": max_search_results_per_assignment,
        "max_follow_up_searches": max_follow_up_searches,
        "max_critic_passes": max_critic_passes,
        "max_run_duration_seconds": max_run_duration_seconds,
    }
    settings = {
        "limits": limits,
        "sources": sources,
        "model_preset": model_preset,
        "models": {k: v.model_dump() for k, v in cfg.models.items()},
    }
    repo = ProjectRepository(opened.engine)
    updated = await repo.update(
        opened.project.id, title=title.strip(), prompt=prompt.strip()
    )
    if updated:
        opened.project = updated
    await repo.create_configuration_snapshot(
        project_id=opened.project.id,
        settings=settings,
        model_presets={k: v.model_dump() for k, v in cfg.models.items()},
        credential_refs={"openrouter": "config", "tavily": "config"},
        label="ui-settings",
    )
    return RedirectResponse(
        f"/projects/{key}?message=Settings+saved", status_code=303
    )


@router.post("/projects/{key}/files")
async def upload_files(
    request: Request,
    key: str,
    files: list[UploadFile] = File(...),
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    importer = LocalFileImporter(opened.engine)
    imported = 0
    for upload in files:
        if not upload.filename:
            continue
        content = await upload.read()
        raw = RawDocument(
            content=content,
            media_type=guess_media_type(upload.filename),
            filename=upload.filename,
            title=Path(upload.filename).stem,
            metadata={"uploaded": True},
        )
        await importer.import_raw(raw, project_id=opened.project.id)
        imported += 1
    return RedirectResponse(
        f"/projects/{key}?message=Imported+{imported}+file(s)", status_code=303
    )


async def _run_workflow_background(
    state: AppState,
    key: str,
    run_id: str,
    *,
    mode: str,
    action: str | None = None,
    feedback: str | None = None,
) -> None:
    """Background asyncio task — continues if the browser disconnects."""
    try:
        opened = await state.open(key)
        handle = await open_workflow(
            opened.engine,
            project_id=opened.project.id,
            run_id=run_id,
            supervisor=FakeSupervisor(),
        )
        events = EventRepository(opened.engine)
        if mode == "start":
            await events.create(
                kind="ui.run.start",
                message="Research run started",
                run_id=run_id,
            )
            await start_until_plan_interrupt(handle)
        elif mode == "resume_approval":
            await events.create(
                kind="ui.plan.decision",
                message=f"Plan {action}",
                run_id=run_id,
                payload={"action": action, "feedback": feedback},
            )
            await resume_with_approval(
                handle, action=action or "approve", feedback=feedback
            )
        elif mode == "resume_interrupted":
            await events.create(
                kind="ui.run.resume",
                message="Resuming interrupted run",
                run_id=run_id,
            )
            recovery = RecoveryService(opened.engine)
            await recovery.resume_run(run_id)
            # Replay from checkpointer at interrupt or continue graph.
            snapshot = await handle.get_state()
            if snapshot and snapshot.next:
                # Still waiting at a node — if plan interrupt, leave awaiting;
                # otherwise poke with empty resume is unsafe. Emit status only.
                await events.create(
                    kind="ui.run.checkpoint",
                    message=f"Checkpoint ready; next={list(snapshot.next)}",
                    run_id=run_id,
                    payload={"next": list(snapshot.next)},
                )
            else:
                await events.create(
                    kind="ui.run.resume.noop",
                    message="No pending graph nodes; run marked running",
                    run_id=run_id,
                )
    except Exception as exc:  # noqa: BLE001 — surface in UI event stream
        try:
            opened = await state.open(key)
            await EventRepository(opened.engine).create(
                kind="ui.run.error",
                message=str(exc),
                run_id=run_id,
                level="error",
            )
            await ErrorRepository(opened.engine).create(
                message=str(exc),
                run_id=run_id,
                code="workflow_error",
            )
            run = await RunRepository(opened.engine).get(run_id)
            if run and run.status in _ACTIVE_RUN:
                await RunRepository(opened.engine).update_status(
                    run_id, RunStatus.FAILED
                )
        except Exception:
            pass
    finally:
        state.release_run(run_id)


@router.post("/projects/{key}/runs")
async def start_run(request: Request, key: str) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    if state.has_active_run():
        return RedirectResponse(
            f"/projects/{key}?error=Another+run+is+already+active",
            status_code=303,
        )
    # Snapshot configuration for the run.
    from sqlalchemy import text

    snapshot_id = None
    async with opened.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id FROM configuration_snapshot "
                    "WHERE project_id = :pid ORDER BY created_at DESC LIMIT 1"
                ),
                {"pid": opened.project.id},
            )
        ).first()
        if row:
            snapshot_id = row[0]

    handle = await open_workflow(
        opened.engine,
        project_id=opened.project.id,
        configuration_snapshot_id=snapshot_id,
        supervisor=FakeSupervisor(),
    )
    run_id = handle.run.id
    await state.claim_run(key, run_id)
    task = asyncio.create_task(
        _run_workflow_background(state, key, run_id, mode="start"),
        name=f"deeprhetor-run-{run_id}",
    )
    state.register_task(run_id, task)
    return RedirectResponse(f"/projects/{key}/plan?run_id={run_id}", status_code=303)


@router.get("/projects/{key}/plan", response_class=HTMLResponse)
async def plan_view(request: Request, key: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    run_id = request.query_params.get("run_id")
    runs = RunRepository(opened.engine)
    plans = ResearchPlanRepository(opened.engine)
    run = await runs.get(run_id) if run_id else None
    if run is None:
        all_runs = await runs.list_for_project(opened.project.id)
        run = all_runs[0] if all_runs else None
    plan = None
    if run:
        plan = await plans.latest_for_run(run.id)
    if plan is None:
        plan = await plans.latest_for_project(opened.project.id)
    return _templates(request).TemplateResponse(
        request,
        "plan.html",
        {
            **_nav(key, "plan"),
            "project": opened.project,
            "run": run,
            "plan": plan,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/projects/{key}/plan/approve")
async def plan_approve(
    request: Request,
    key: str,
    run_id: str = Form(...),
    action: str = Form(...),
    feedback: str = Form(""),
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    run = await RunRepository(opened.engine).get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if action not in {"approve", "feedback"}:
        return RedirectResponse(
            f"/projects/{key}/plan?run_id={run_id}&error=Invalid+action",
            status_code=303,
        )
    if action == "feedback" and not feedback.strip():
        return RedirectResponse(
            f"/projects/{key}/plan?run_id={run_id}&error=Feedback+required",
            status_code=303,
        )
    if state.has_active_run() and state.active_run_id != run_id:
        return RedirectResponse(
            f"/projects/{key}/plan?run_id={run_id}&error=Another+run+is+active",
            status_code=303,
        )
    await state.claim_run(key, run_id)
    task = asyncio.create_task(
        _run_workflow_background(
            state,
            key,
            run_id,
            mode="resume_approval",
            action=action,
            feedback=feedback.strip() or None,
        ),
        name=f"deeprhetor-approve-{run_id}",
    )
    state.register_task(run_id, task)
    if action == "approve":
        return RedirectResponse(f"/projects/{key}/runs/{run_id}", status_code=303)
    return RedirectResponse(
        f"/projects/{key}/plan?run_id={run_id}&message=Feedback+sent",
        status_code=303,
    )


@router.get("/projects/{key}/runs/{run_id}", response_class=HTMLResponse)
async def run_live(request: Request, key: str, run_id: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    run = await RunRepository(opened.engine).get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    tasks = await TaskRepository(opened.engine).list_for_run(run_id)
    events = await EventRepository(opened.engine).list_for_run(run_id)
    errors = await ErrorRepository(opened.engine).list_for_run(run_id)
    docs = await DocumentRepository(opened.engine).list_for_project(opened.project.id)
    claims = await ClaimRepository(opened.engine).list_for_project(
        opened.project.id, run_id=run_id
    )
    scans = await ScanRepository(opened.engine).list_incomplete_document_scans()
    return _templates(request).TemplateResponse(
        request,
        "run.html",
        {
            **_nav(key, "run"),
            "project": opened.project,
            "run": run,
            "tasks": tasks,
            "events": events[-100:],
            "errors": errors,
            "documents": docs,
            "claims": claims,
            "incomplete_scans": scans,
        },
    )


@router.get("/runs/{run_id}/events")
async def run_events_sse(
    request: Request,
    run_id: str,
    after: str | None = None,
) -> StreamingResponse:
    """SSE stream of run events (also reachable under project path)."""
    state = _state(request)
    # Find an opened project that owns this run, or open files until found.
    opened = None
    if state.active_project_key:
        try:
            opened = await state.open(state.active_project_key)
        except FileNotFoundError:
            opened = None
    if opened is None:
        for listed in await state.list_projects():
            op = await state.open(listed.key)
            run = await RunRepository(op.engine).get(run_id)
            if run is not None:
                opened = op
                break
    if opened is None:
        raise HTTPException(404, "run not found")

    events_repo = EventRepository(opened.engine)
    runs_repo = RunRepository(opened.engine)

    async def event_generator():
        cursor = after
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            batch = await events_repo.list_for_run_after(
                run_id, after_id=cursor, limit=50
            )
            for ev in batch:
                cursor = ev.id
                payload = {
                    "id": ev.id,
                    "kind": ev.kind,
                    "level": ev.level,
                    "message": ev.message,
                    "task_id": ev.task_id,
                    "created_at": ev.created_at.isoformat(),
                    "payload": ev.payload,
                }
                yield f"id: {ev.id}\nevent: message\ndata: {json.dumps(payload)}\n\n"
                idle = 0
            run = await runs_repo.get(run_id)
            status = str(run.status) if run else "unknown"
            yield (
                f"event: run.status\ndata: "
                f"{json.dumps({'run_id': run_id, 'status': status})}\n\n"
            )
            if run and run.status not in _ACTIVE_RUN and not batch:
                idle += 1
                if idle >= 3:
                    yield f"event: stream.end\ndata: {json.dumps({'run_id': run_id})}\n\n"
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{key}/runs/{run_id}/events")
async def run_events_sse_nested(
    request: Request, key: str, run_id: str, after: str | None = None
) -> StreamingResponse:
    # Ensure project is opened for the shorthand lookup path.
    await _project_or_404(_state(request), key)
    return await run_events_sse(request, run_id, after=after)


@router.get("/projects/{key}/corpus", response_class=HTMLResponse)
async def corpus_view(request: Request, key: str) -> HTMLResponse:
    from deeprhetor.repositories.knowledge import EvidenceRepository

    state = _state(request)
    opened = await _project_or_404(state, key)
    docs = await DocumentRepository(opened.engine).list_for_project(opened.project.id)
    claims = await ClaimRepository(opened.engine).list_for_project(opened.project.id)
    claim_id = request.query_params.get("claim_id")
    selected = None
    evidence_pairs: list = []
    if claim_id:
        selected = await ClaimRepository(opened.engine).get(claim_id)
        if selected is not None:
            evidence_pairs = await EvidenceRepository(opened.engine).list_for_claim(
                selected.id
            )
    doc_rows = []
    scans = ScanRepository(opened.engine)
    docs_repo = DocumentRepository(opened.engine)
    for doc in docs:
        version = await docs_repo.latest_version(doc.id)
        scan = None
        seg_count = 0
        if version:
            scan = await scans.get_document_scan(version.id)
            segs = await docs_repo.list_segments(version.id)
            seg_count = len(segs)
        doc_rows.append(
            {"doc": doc, "version": version, "scan": scan, "segments": seg_count}
        )
    return _templates(request).TemplateResponse(
        request,
        "corpus.html",
        {
            **_nav(key, "corpus"),
            "project": opened.project,
            "documents": doc_rows,
            "claims": claims,
            "selected_claim": selected,
            "evidence_pairs": evidence_pairs,
        },
    )


@router.get("/projects/{key}/draft", response_class=HTMLResponse)
async def draft_view(request: Request, key: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    outline = await OutlineRepository(opened.engine).latest_for_project(
        opened.project.id
    )
    draft = await DraftRepository(opened.engine).latest_for_project(opened.project.id)
    validation = None
    if draft:
        validation = await ValidationResultRepository(opened.engine).latest_for_draft(
            draft.id
        )
    return _templates(request).TemplateResponse(
        request,
        "draft.html",
        {
            **_nav(key, "draft"),
            "project": opened.project,
            "outline": outline,
            "draft": draft,
            "validation": validation,
        },
    )


@router.get("/projects/{key}/export", response_class=HTMLResponse)
async def export_view(request: Request, key: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    artifacts = await ArtifactRepository(opened.engine).list_for_project(
        opened.project.id
    )
    return _templates(request).TemplateResponse(
        request,
        "export.html",
        {
            **_nav(key, "export"),
            "project": opened.project,
            "artifacts": artifacts,
            "pdf_artifacts": [a for a in artifacts if a.kind == "pdf" or (a.media_type or "").endswith("pdf")],
            "other_artifacts": [
                a
                for a in artifacts
                if not (a.kind == "pdf" or (a.media_type or "").endswith("pdf"))
            ],
        },
    )


@router.get("/projects/{key}/artifacts/{artifact_id}")
async def download_artifact(
    request: Request, key: str, artifact_id: str
) -> Response:
    state = _state(request)
    opened = await _project_or_404(state, key)
    repo = ArtifactRepository(opened.engine)
    art = await repo.get(artifact_id)
    if art is None or art.project_id != opened.project.id:
        raise HTTPException(404, "artifact not found")
    data = await repo.get_data(artifact_id)
    if data is None:
        raise HTTPException(404, "artifact has no stored bytes")
    filename = art.path_or_name or f"{art.kind}-{art.id}"
    media = art.media_type or "application/octet-stream"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{key}/recovery", response_class=HTMLResponse)
async def recovery_view(request: Request, key: str) -> HTMLResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    runs = await RunRepository(opened.engine).list_for_project(opened.project.id)
    interrupted = [
        r
        for r in runs
        if r.status in {RunStatus.INTERRUPTED, RunStatus.FAILED}
    ]
    tasks_by_run: dict[str, list] = {}
    task_repo = TaskRepository(opened.engine)
    for run in interrupted:
        tasks = await task_repo.list_for_run(run.id)
        tasks_by_run[run.id] = [
            t
            for t in tasks
            if t.status.value in {"interrupted", "failed"}
        ]
    return _templates(request).TemplateResponse(
        request,
        "recovery.html",
        {
            **_nav(key, "recovery"),
            "project": opened.project,
            "interrupted_runs": interrupted,
            "tasks_by_run": tasks_by_run,
            "recovery": opened.recovery,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/projects/{key}/recovery/{run_id}/resume")
async def recovery_resume(
    request: Request, key: str, run_id: str
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    recovery = RecoveryService(opened.engine)
    try:
        await recovery.resume_run(run_id)
    except (LookupError, ValueError) as exc:
        return RedirectResponse(
            f"/projects/{key}/recovery?error={str(exc)}", status_code=303
        )
    if state.has_active_run() and state.active_run_id != run_id:
        return RedirectResponse(
            f"/projects/{key}/recovery?error=Another+run+is+active",
            status_code=303,
        )
    await state.claim_run(key, run_id)
    task = asyncio.create_task(
        _run_workflow_background(state, key, run_id, mode="resume_interrupted"),
        name=f"deeprhetor-resume-{run_id}",
    )
    state.register_task(run_id, task)
    return RedirectResponse(f"/projects/{key}/runs/{run_id}", status_code=303)


@router.post("/projects/{key}/recovery/{run_id}/abandon")
async def recovery_abandon(
    request: Request, key: str, run_id: str
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    recovery = RecoveryService(opened.engine)
    try:
        await recovery.abandon_run(run_id)
    except (LookupError, ValueError) as exc:
        return RedirectResponse(
            f"/projects/{key}/recovery?error={str(exc)}", status_code=303
        )
    return RedirectResponse(
        f"/projects/{key}/recovery?message=Run+abandoned", status_code=303
    )


@router.post("/projects/{key}/recovery/tasks/{task_id}/retry")
async def recovery_retry_task(
    request: Request, key: str, task_id: str
) -> RedirectResponse:
    state = _state(request)
    opened = await _project_or_404(state, key)
    recovery = RecoveryService(opened.engine)
    try:
        task = await recovery.retry_task(task_id)
    except (LookupError, ValueError) as exc:
        return RedirectResponse(
            f"/projects/{key}/recovery?error={str(exc)}", status_code=303
        )
    return RedirectResponse(
        f"/projects/{key}/recovery?message=Retried+task+{task.id}",
        status_code=303,
    )
