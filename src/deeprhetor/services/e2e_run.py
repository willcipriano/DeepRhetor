"""End-to-end live research → publication runner."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deeprhetor.config.loader import load_config
from deeprhetor.domain.enums import ClaimStatus, PlanStatus, RunStatus
from deeprhetor.repositories.operations import ArtifactRepository
from deeprhetor.repositories.planning import ResearchPlanRepository
from deeprhetor.services.outline import OutlineBuilderService
from deeprhetor.services.project_store import create_project_async, open_project_async
from deeprhetor.services.publish import PublicationService
from deeprhetor.workflow.live import build_live_components
from deeprhetor.workflow.runtime import (
    open_workflow,
    resume_with_approval,
    start_until_plan_interrupt,
)

logger = logging.getLogger(__name__)


@dataclass
class E2EResult:
    project_path: Path
    project_id: str
    run_id: str
    plan_id: str | None
    approved_claims: int
    documents: int
    export_dir: Path
    publication_status: str | None
    tex_path: Path | None
    bib_path: Path | None
    manifest_path: Path | None
    pdf_path: Path | None
    supervisor_fallback: bool
    supervisor_error: str | None
    worker_summaries: list[dict[str, Any]]


async def run_end_to_end(
    *,
    prompt: str,
    title: str | None = None,
    project_path: Path | str,
    export_dir: Path | str | None = None,
    auto_approve: bool = True,
) -> E2EResult:
    """Create a project, run the live workflow, publish tex/bib/manifest."""
    path = Path(project_path)
    cfg = load_config()
    title = title or prompt[:80]
    export = Path(export_dir) if export_dir else path.with_suffix("") / "exports"
    export.mkdir(parents=True, exist_ok=True)

    if path.exists():
        opened = await open_project_async(path)
    else:
        opened = await create_project_async(
            path,
            title=title,
            prompt=prompt,
            config_snapshot={
                "models": {
                    name: p.model_dump(mode="json") for name, p in cfg.models.items()
                },
                "limits": cfg.limits.model_dump(mode="json"),
                "live_e2e": True,
            },
        )

    project_id = opened.project.id
    live = build_live_components(
        engine=opened.engine, project_id=project_id, config=cfg
    )

    handle = await open_workflow(
        opened.engine,
        project_id=project_id,
        supervisor=live["supervisor"],
        worker=live["worker"],
        proposer=live["proposer"],
        critic=live["critic"],
        providers=live["providers"],
        limits=live["limits"],
    )

    logger.info("Starting workflow for project %s run %s", project_id, handle.run.id)
    await start_until_plan_interrupt(handle)

    state = await handle.get_state()
    logger.info(
        "Interrupted for plan approval; next=%s values.stage=%s",
        state.next,
        (state.values or {}).get("stage"),
    )

    if not auto_approve:
        raise RuntimeError("auto_approve=False; approve the plan via the UI/API")

    await resume_with_approval(handle, action="approve")
    await handle.ctx.runs.update_status(handle.run.id, RunStatus.COMPLETED)

    plans = ResearchPlanRepository(opened.engine)
    stored_plans = await plans.list_for_project(project_id)
    approved = [p for p in stored_plans if p.plan.status == PlanStatus.APPROVED]
    plan_row = approved[-1] if approved else (stored_plans[-1] if stored_plans else None)
    if plan_row is None:
        raise RuntimeError("No research plan persisted after approval")

    claims = handle.ctx.claims
    approved_claims = await claims.list_by_status(project_id, [ClaimStatus.APPROVED])
    docs = await handle.ctx.documents.list_for_project(project_id)

    logger.info(
        "Research done: %s docs, %s approved claims; building publication",
        len(docs),
        len(approved_claims),
    )

    outline_svc = OutlineBuilderService(opened.engine)
    stored_outline = await outline_svc.build_and_persist(
        project_id=project_id,
        plan=plan_row.plan,
        title=title,
    )
    from deeprhetor.services.llm_writer import OpenRouterWriter

    writer = OpenRouterWriter(config=cfg, engine=opened.engine)
    stored_draft, citation_map = await writer.build_and_persist(
        project_id=project_id,
        outline=stored_outline.outline,
        abstract=f"Evidence-backed report for: {prompt}",
    )
    publisher = PublicationService(opened.engine)
    publication = await publisher.publish(
        stored_draft.draft,
        project_id=project_id,
        outline=stored_outline.outline,
        citation_map=citation_map,
        run_id=handle.run.id,
        compile_pdf=True,
    )

    artifacts = ArtifactRepository(opened.engine)
    tex_path = bib_path = manifest_path = pdf_path = None
    if publication.tex_artifact_id:
        data = await artifacts.get_data(publication.tex_artifact_id)
        if data:
            tex_path = export / "report.tex"
            tex_path.write_bytes(data)
    if publication.bib_artifact_id:
        data = await artifacts.get_data(publication.bib_artifact_id)
        if data:
            bib_path = export / "report.bib"
            bib_path.write_bytes(data)
            # Match the scholarly template's \addbibresource{refs.bib}
            (export / "refs.bib").write_bytes(data)
    if publication.manifest_artifact_id:
        data = await artifacts.get_data(publication.manifest_artifact_id)
        if data:
            manifest_path = export / "provenance.json"
            manifest_path.write_bytes(data)
    if publication.pdf_artifact_id:
        data = await artifacts.get_data(publication.pdf_artifact_id)
        if data:
            pdf_path = export / "report.pdf"
            pdf_path.write_bytes(data)

    summary = {
        "project_id": project_id,
        "run_id": handle.run.id,
        "plan_id": plan_row.id,
        "documents": len(docs),
        "approved_claims": len(approved_claims),
        "publication_status": str(publication.status),
        "supervisor_fallback": getattr(live["supervisor"], "used_fallback", False),
        "claim_proposer_fallback": getattr(live["proposer"], "used_fallback", False),
        "claim_proposer_error": getattr(live["proposer"], "last_error", None),
        "writer_fallback": getattr(writer, "used_fallback", False),
        "writer_error": getattr(writer, "last_error", None),
        "worker_results": getattr(live["worker"], "results", []),
    }
    (export / "e2e_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    await opened.engine.dispose()

    return E2EResult(
        project_path=path,
        project_id=project_id,
        run_id=handle.run.id,
        plan_id=plan_row.id,
        approved_claims=len(approved_claims),
        documents=len(docs),
        export_dir=export,
        publication_status=str(publication.status),
        tex_path=tex_path,
        bib_path=bib_path,
        manifest_path=manifest_path,
        pdf_path=pdf_path,
        supervisor_fallback=bool(getattr(live["supervisor"], "used_fallback", False)),
        supervisor_error=getattr(live["supervisor"], "last_error", None),
        worker_summaries=list(getattr(live["worker"], "results", [])),
    )
