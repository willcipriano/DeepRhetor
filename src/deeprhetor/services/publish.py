"""End-to-end publication: validate → render → compile → export artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncEngine

from deeprhetor.domain.enums import PublicationStatus, ValidationOutcome
from deeprhetor.domain.publication import ProvenanceManifest, PublicationResult, ValidationResult
from deeprhetor.domain.writing import CitationKey, Outline, StructuredDraft
from deeprhetor.repositories.operations import ArtifactRepository
from deeprhetor.repositories.writing import CitationKeyRepository, ValidationResultRepository
from deeprhetor.services.citation_validate import CitationValidator
from deeprhetor.services.latex import LatexRenderer, which_toolchain


class PublicationService:
    """Validate a structured draft and export .tex / .bib / PDF / provenance."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        validator: CitationValidator | None = None,
        renderer: LatexRenderer | None = None,
        artifacts: ArtifactRepository | None = None,
        citations: CitationKeyRepository | None = None,
        validations: ValidationResultRepository | None = None,
    ) -> None:
        self._engine = engine
        self.validator = validator or CitationValidator(engine)
        self.renderer = renderer or LatexRenderer()
        self.artifacts = artifacts or ArtifactRepository(engine)
        self.citations = citations or CitationKeyRepository(engine)
        self.validations = validations or ValidationResultRepository(engine)

    async def publish(
        self,
        draft: StructuredDraft,
        *,
        project_id: str,
        outline: Outline | None = None,
        citation_map: Mapping[str, CitationKey] | None = None,
        run_id: str | None = None,
        compile_pdf: bool = True,
    ) -> PublicationResult:
        if citation_map is None:
            stored = await self.citations.list_for_project(project_id)
            citation_map = {c.key: c for c in stored}

        validation = await self.validator.validate(
            draft,
            project_id=project_id,
            outline=outline,
            citation_map=citation_map,
            persist=True,
        )
        if validation.outcome == ValidationOutcome.FAILED:
            report_id = await self._store_validation_report(
                project_id=project_id, run_id=run_id, validation=validation
            )
            return PublicationResult(
                draft_id=draft.id,
                validation_id=validation.id,
                status=PublicationStatus.FAILED,
                validation_report_artifact_id=report_id,
                error_message="citation validation failed",
            )

        rendered = self.renderer.render(draft, citation_map)
        tex_art = await self.artifacts.create(
            project_id=project_id,
            run_id=run_id,
            kind="latex",
            media_type="application/x-tex",
            path_or_name=f"{draft.id}.tex",
            sha256=rendered.tex_sha256,
            data=rendered.tex.encode("utf-8"),
            idempotency_key=f"tex:{draft.id}:{rendered.tex_sha256}",
        )
        bib_art = await self.artifacts.create(
            project_id=project_id,
            run_id=run_id,
            kind="bibtex",
            media_type="application/x-bibtex",
            path_or_name=f"{draft.id}.bib",
            sha256=rendered.bib_sha256,
            data=rendered.bib.encode("utf-8"),
            idempotency_key=f"bib:{draft.id}:{rendered.bib_sha256}",
        )

        pdf_art_id = None
        pdf_compiled = False
        pdf_skip = None
        status = PublicationStatus.RENDERED
        if compile_pdf:
            compile_result = self.renderer.compile_pdf(rendered)
            if compile_result.skipped:
                pdf_skip = compile_result.skip_reason
            elif compile_result.pdf_path is not None:
                pdf_bytes = compile_result.pdf_path.read_bytes()
                pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
                pdf_art = await self.artifacts.create(
                    project_id=project_id,
                    run_id=run_id,
                    kind="pdf",
                    media_type="application/pdf",
                    path_or_name=f"{draft.id}.pdf",
                    sha256=pdf_sha,
                    data=pdf_bytes,
                    idempotency_key=f"pdf:{draft.id}:{pdf_sha}",
                )
                pdf_art_id = pdf_art.id
                pdf_compiled = True
                status = PublicationStatus.COMPILED
            else:
                pdf_skip = compile_result.log or "pdf compile failed"
                status = PublicationStatus.RENDERED

        report_id = await self._store_validation_report(
            project_id=project_id, run_id=run_id, validation=validation
        )

        claim_ids: list[str] = []
        evidence_ids: list[str] = []
        document_ids: list[str] = []
        doc_hashes: dict[str, str] = {}
        cite_keys: list[str] = []
        for key in sorted(set(draft.bibliography_keys) | {k for s in draft.sections for k in s.citation_keys}):
            cite = citation_map.get(key)
            if cite is None:
                continue
            cite_keys.append(key)
            if cite.claim_id:
                claim_ids.append(cite.claim_id)
            if cite.evidence_id:
                evidence_ids.append(cite.evidence_id)
            if cite.document_id:
                document_ids.append(cite.document_id)
            if cite.document_id and cite.document_content_sha256:
                doc_hashes[cite.document_id] = cite.document_content_sha256

        tools = which_toolchain()
        artifact_ids = {
            "tex": tex_art.id,
            "bib": bib_art.id,
            "validation_report": report_id,
        }
        if pdf_art_id:
            artifact_ids["pdf"] = pdf_art_id

        manifest = ProvenanceManifest(
            project_id=project_id,
            draft_id=draft.id,
            outline_id=outline.id if outline else draft.outline_id,
            plan_id=outline.plan_id if outline else None,
            validation_id=validation.id,
            title=draft.title,
            citation_keys=cite_keys,
            claim_ids=sorted(set(claim_ids)),
            evidence_ids=sorted(set(evidence_ids)),
            document_ids=sorted(set(document_ids)),
            document_content_hashes=doc_hashes,
            artifact_ids=artifact_ids,
            toolchain={
                "tectonic": tools["tectonic"],
                "pandoc": tools["pandoc"],
                "pdf_compiled": pdf_compiled,
                "pdf_skipped_reason": pdf_skip,
            },
            validation_outcome=validation.outcome,
        )
        manifest_bytes = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True).encode(
            "utf-8"
        )
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_art = await self.artifacts.create(
            project_id=project_id,
            run_id=run_id,
            kind="provenance_manifest",
            media_type="application/json",
            path_or_name=f"{draft.id}.provenance.json",
            sha256=manifest_sha,
            data=manifest_bytes,
            idempotency_key=f"manifest:{draft.id}:{manifest_sha}",
        )
        artifact_ids["manifest"] = manifest_art.id
        manifest = manifest.model_copy(update={"artifact_ids": dict(artifact_ids)})

        return PublicationResult(
            draft_id=draft.id,
            validation_id=validation.id,
            status=status,
            pdf_artifact_id=pdf_art_id,
            tex_artifact_id=tex_art.id,
            bib_artifact_id=bib_art.id,
            manifest_artifact_id=manifest_art.id,
            validation_report_artifact_id=report_id,
            pdf_compiled=pdf_compiled,
            pdf_skipped_reason=pdf_skip,
            manifest=manifest,
            tex=rendered.tex,
            bib=rendered.bib,
        )

    async def _store_validation_report(
        self,
        *,
        project_id: str,
        run_id: str | None,
        validation: ValidationResult,
    ) -> str:
        payload = json.dumps(validation.model_dump(mode="json"), indent=2, sort_keys=True).encode(
            "utf-8"
        )
        sha = hashlib.sha256(payload).hexdigest()
        art = await self.artifacts.create(
            project_id=project_id,
            run_id=run_id,
            kind="validation_report",
            media_type="application/json",
            path_or_name=f"{validation.draft_id}.validation.json",
            sha256=sha,
            data=payload,
            idempotency_key=f"validation:{validation.id}:{sha}",
        )
        return art.id
