"""AI typesetting: Markdown draft → curated LaTeX section bodies."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.writing import (
    CitationKey,
    MarkdownDraft,
    TypesetDocument,
    TypesetSection,
)
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import RoleName
from deeprhetor.services.citation_validate import (
    UNSAFE_LATEX_PATTERNS,
    extract_markdown_citation_keys,
)
from deeprhetor.services.latex import escape_latex

logger = logging.getLogger(__name__)


class TypesetSectionOut(BaseModel):
    section_id: str
    title: str
    body_latex: str = Field(
        description=(
            "LaTeX body for this section only (no \\section, no preamble). "
            "Use \\footnote{\\cite{key}} for citations that appear in the markdown."
        )
    )
    order: int = 0


class TypesetDocumentOut(BaseModel):
    title: str
    abstract_latex: str | None = None
    sections: list[TypesetSectionOut] = Field(default_factory=list)
    bibliography_keys: list[str] = Field(default_factory=list)


@dataclass
class LatexTypesetter:
    """Convert a Markdown draft into template-ready LaTeX section bodies via AI.

    The curated scholarly preamble remains template-owned. Models must not emit
    \\documentclass / \\usepackage. Falls back to a deterministic MD→TeX converter.
    """

    config: AppConfig | None = None
    last_error: str | None = None
    used_fallback: bool = False

    async def typeset(
        self,
        draft: MarkdownDraft,
        *,
        allowed_keys: set[str] | list[str],
    ) -> TypesetDocument:
        allowed = set(allowed_keys)
        try:
            if self.config is None:
                raise RuntimeError("AppConfig required for AI typesetting")
            return await self._typeset_llm(draft, allowed_keys=allowed)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.used_fallback = True
            logger.warning("AI typesetter failed (%s); using deterministic MD→TeX", exc)
            return deterministic_markdown_typeset(draft, allowed_keys=allowed)

    async def _typeset_llm(
        self,
        draft: MarkdownDraft,
        *,
        allowed_keys: set[str],
    ) -> TypesetDocument:
        assert self.config is not None
        registry = ModelRegistry(self.config)
        # Mid-tier typesetter (outline_editor lane), not the frontier writer.
        model = registry.build_model_for_role(RoleName.OUTLINE_EDITOR)
        agent: Agent[None, TypesetDocumentOut] = Agent(
            model,
            output_type=TypesetDocumentOut,
            instructions=(
                "You are DeepRhetor's typesetter. Convert scholarly Markdown into LaTeX "
                "section bodies for a curated template. Rules:\n"
                "- Output ONLY section titles + body_latex fragments (no documentclass, "
                "no usepackage, no begin{document}).\n"
                "- Convert Markdown headings to separate sections.\n"
                "- Convert citation markers [@key] or [^key] into "
                "\\footnote{\\cite{key}} using only allowed citation keys.\n"
                "- Escape TeX special characters in prose.\n"
                "- Preserve scholarly meaning; do not invent citations or facts.\n"
                "- Prefer \\emph{} for emphasis; avoid raw HTML."
            ),
            name="typesetter",
        )
        prompt = (
            f"Allowed citation keys: {sorted(allowed_keys)}\n\n"
            f"Title: {draft.title}\n"
            f"Abstract (markdown/plain): {draft.abstract or ''}\n\n"
            f"Markdown document:\n{draft.markdown}\n"
        )
        result = await agent.run(prompt)
        out = result.output
        if not isinstance(out, TypesetDocumentOut):
            out = TypesetDocumentOut.model_validate(out)

        sections: list[TypesetSection] = []
        for idx, section in enumerate(out.sections):
            body = section.body_latex or ""
            _assert_safe_typeset_body(body, path=f"sections[{idx}].body_latex")
            body = _filter_cites(body, allowed_keys)
            sections.append(
                TypesetSection(
                    section_id=section.section_id or f"sec_{idx}",
                    title=section.title,
                    body_latex=body,
                    order=section.order if section.order else idx,
                )
            )
        if not sections:
            raise RuntimeError("typesetter returned no sections")

        abstract = out.abstract_latex
        if abstract:
            _assert_safe_typeset_body(abstract, path="abstract_latex")

        bib_keys = [k for k in (out.bibliography_keys or list(draft.bibliography_keys)) if k in allowed_keys]
        if not bib_keys:
            bib_keys = sorted(extract_markdown_citation_keys(draft.markdown) & allowed_keys)

        return TypesetDocument(
            title=out.title or draft.title,
            abstract_latex=abstract or (escape_latex(draft.abstract) if draft.abstract else None),
            sections=sections,
            bibliography_keys=bib_keys,
        )


def deterministic_markdown_typeset(
    draft: MarkdownDraft,
    *,
    allowed_keys: set[str],
) -> TypesetDocument:
    """Offline MD → TeX body conversion for tests and AI fallback."""
    lines = (draft.markdown or "").replace("\r\n", "\n").split("\n")
    sections: list[TypesetSection] = []
    current_title = "Body"
    current_id = "sec_body"
    buf: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order, buf, current_title, current_id
        if not buf and not sections:
            return
        body = _md_blocks_to_latex("\n".join(buf).strip(), allowed_keys)
        sections.append(
            TypesetSection(
                section_id=current_id,
                title=current_title,
                body_latex=body,
                order=order,
            )
        )
        order += 1
        buf = []

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
        if heading:
            flush()
            current_title = heading.group(2).strip()
            current_id = "sec_" + re.sub(r"[^a-z0-9]+", "_", current_title.lower()).strip("_")
            continue
        buf.append(line)
    flush()

    if not sections:
        sections.append(
            TypesetSection(
                section_id="sec_body",
                title="Body",
                body_latex=_md_blocks_to_latex(draft.markdown or "", allowed_keys),
                order=0,
            )
        )

    bib_keys = sorted(extract_markdown_citation_keys(draft.markdown) & allowed_keys)
    return TypesetDocument(
        title=draft.title,
        abstract_latex=escape_latex(draft.abstract) if draft.abstract else None,
        sections=sections,
        bibliography_keys=bib_keys or list(draft.bibliography_keys),
    )


def _md_blocks_to_latex(text: str, allowed_keys: set[str]) -> str:
    if not text.strip():
        return ""
    parts: list[str] = []
    for para in re.split(r"\n\s*\n", text.strip()):
        chunk = para.strip()
        if not chunk:
            continue
        footnotes: list[str] = []

        def _cite_sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in allowed_keys:
                footnotes.append(rf"\footnote{{\cite{{{key}}}}}")
            return ""

        chunk = re.sub(r"\[@([A-Za-z0-9_.:-]+)\]", _cite_sub, chunk)
        chunk = re.sub(r"\[\^([A-Za-z0-9_.:-]+)\]", _cite_sub, chunk)
        # Strip markdown emphasis markers; escape remaining prose.
        chunk = re.sub(r"\*\*(.+?)\*\*", r"\1", chunk)
        chunk = re.sub(r"\*(.+?)\*", r"\1", chunk)
        chunk = re.sub(r"`([^`]+)`", r"\1", chunk)
        chunk = escape_latex(chunk) + "".join(footnotes)
        parts.append(chunk)
    return "\n\n".join(parts)


def _filter_cites(body: str, allowed_keys: set[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in allowed_keys:
            return ""
        return match.group(0)

    return re.sub(r"\\cite\{([A-Za-z0-9_.:-]+)\}", repl, body)


def _assert_safe_typeset_body(text: str, *, path: str) -> None:
    for code, pattern in UNSAFE_LATEX_PATTERNS:
        if pattern.search(text or ""):
            raise ValueError(f"unsafe LaTeX in typeset output ({code}) at {path}")
