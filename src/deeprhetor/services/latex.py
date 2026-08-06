"""Controlled LaTeX rendering and sandboxed PDF compilation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Mapping

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

from deeprhetor.domain.writing import CitationKey, StructuredDraft

# Characters that must be escaped when embedding model prose into TeX.
_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "#": r"\#",
    "$": r"\$",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


@dataclass(frozen=True)
class RenderedLatex:
    tex: str
    bib: str
    tex_sha256: str
    bib_sha256: str


@dataclass(frozen=True)
class CompileResult:
    pdf_path: Path | None
    skipped: bool
    skip_reason: str | None
    log: str = ""
    work_dir: Path | None = None


def escape_latex(text: str) -> str:
    """Escape TeX special characters in prose (citations are appended separately)."""
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in text)


def _template_env() -> Environment:
    template_root = resources.files("deeprhetor").joinpath("templates/latex")
    mapping: dict[str, str] = {}
    for name in ("scholarly.tex.j2", "refs.bib.j2"):
        mapping[name] = template_root.joinpath(name).read_text(encoding="utf-8")

    loaders: list = [DictLoader(mapping)]
    try:
        root_path = Path(str(template_root))
        if root_path.is_dir():
            loaders.insert(0, FileSystemLoader(str(root_path)))
    except (TypeError, OSError):
        pass

    return Environment(
        loader=ChoiceLoader(loaders),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def which_toolchain() -> dict[str, str | None]:
    return {
        "tectonic": shutil.which("tectonic"),
        "pandoc": shutil.which("pandoc"),
    }


def toolchain_ready() -> bool:
    tools = which_toolchain()
    return bool(tools["tectonic"] and tools["pandoc"])


def _sanitize_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_:.-]+", "_", value)
    return cleaned or "section"


def _bib_brace(value: str) -> str:
    """Escape BibTeX specials inside braced fields."""
    return (
        value.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
    )


class LatexRenderer:
    """Generate curated scholarly TeX + deterministic BibTeX from structured drafts."""

    def __init__(self, *, author: str = "DeepRhetor", date_str: str | None = None) -> None:
        self.author = author
        self.date_str = date_str or date.today().isoformat()
        self._env = _template_env()

    def render_bib(self, citations: Mapping[str, CitationKey] | list[CitationKey]) -> str:
        if isinstance(citations, Mapping):
            entries = [citations[k] for k in sorted(citations.keys())]
        else:
            entries = sorted(citations, key=lambda c: c.key)
        bib_rows = []
        for cite in entries:
            bib = cite.bib.model_copy(update={"key": cite.key})
            entry_type = bib.entry_type
            if bib.url and entry_type in {"misc", ""}:
                entry_type = "online"
            bib_rows.append(
                {
                    "entry_type": entry_type or "misc",
                    "key": bib.key,
                    "title": _bib_brace(bib.title or cite.key),
                    "author": _bib_brace(bib.author) if bib.author else "",
                    "year": _bib_brace(bib.year) if bib.year else "",
                    "url": bib.url or "",
                    "doi": bib.doi or "",
                    "publisher": _bib_brace(bib.publisher) if bib.publisher else "",
                    "howpublished": _bib_brace(bib.howpublished) if bib.howpublished else "",
                    "urldate": bib.urldate or "",
                    "note": _bib_brace(bib.note) if bib.note else "",
                    "extra": {k: _bib_brace(v) for k, v in bib.extra.items()},
                }
            )
        return self._env.get_template("refs.bib.j2").render(entries=bib_rows)

    def render_tex(
        self,
        draft: StructuredDraft,
        citations: Mapping[str, CitationKey],
    ) -> str:
        sections = []
        for section in sorted(draft.sections, key=lambda s: s.order):
            body = escape_latex(section.prose)
            footnotes: list[str] = []
            for key in section.citation_keys:
                if key not in citations:
                    continue
                footnotes.append(rf"\footnote{{\cite{{{key}}}}}")
            if footnotes:
                body = body.rstrip() + "".join(footnotes)
            sections.append(
                {
                    "section_id": _sanitize_label(section.section_id),
                    "title": escape_latex(section.title),
                    "body": body,
                }
            )
        abstract = escape_latex(draft.abstract) if draft.abstract else ""
        return self._env.get_template("scholarly.tex.j2").render(
            title=escape_latex(draft.title),
            author=escape_latex(self.author),
            date=escape_latex(self.date_str),
            abstract=abstract,
            sections=sections,
        )

    def render(
        self,
        draft: StructuredDraft,
        citations: Mapping[str, CitationKey],
    ) -> RenderedLatex:
        used_keys = set(draft.bibliography_keys)
        for section in draft.sections:
            used_keys.update(section.citation_keys)
        subset = {k: citations[k] for k in sorted(used_keys) if k in citations}
        tex = self.render_tex(draft, citations)
        bib = self.render_bib(subset)
        return RenderedLatex(
            tex=tex,
            bib=bib,
            tex_sha256=hashlib.sha256(tex.encode("utf-8")).hexdigest(),
            bib_sha256=hashlib.sha256(bib.encode("utf-8")).hexdigest(),
        )

    def compile_pdf(
        self,
        rendered: RenderedLatex,
        *,
        work_dir: Path | None = None,
        timeout_sec: float = 180.0,
    ) -> CompileResult:
        """Compile with Pandoc+Tectonic in an isolated temp dir (shell-escape disabled)."""
        tools = which_toolchain()
        missing = [name for name, path in tools.items() if not path]
        if missing:
            return CompileResult(
                pdf_path=None,
                skipped=True,
                skip_reason=f"missing toolchain binaries: {', '.join(missing)}",
            )

        root = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="deeprhetor_tex_"))
        root.mkdir(parents=True, exist_ok=True)
        tex_path = root / "main.tex"
        bib_path = root / "refs.bib"
        tex_path.write_text(rendered.tex, encoding="utf-8")
        bib_path.write_text(rendered.bib, encoding="utf-8")

        logs: list[str] = []
        env = os.environ.copy()
        # Defense in depth: unset classic TeX shell-escape opt-in.
        env.pop("TEXINPUTS_shell_escape", None)
        env["source_date_epoch"] = env.get("SOURCE_DATE_EPOCH", "0")

        pandoc = tools["pandoc"]
        assert pandoc is not None
        pandoc_cmd = [
            pandoc,
            str(tex_path),
            "-f",
            "latex",
            "-t",
            "latex",
            "-o",
            str(root / "pandoc-check.tex"),
        ]
        try:
            pandoc_proc = subprocess.run(
                pandoc_cmd,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CompileResult(
                pdf_path=None,
                skipped=False,
                skip_reason=None,
                log=f"pandoc failed: {exc}",
                work_dir=root,
            )
        logs.append(pandoc_proc.stdout or "")
        logs.append(pandoc_proc.stderr or "")
        if pandoc_proc.returncode != 0:
            return CompileResult(
                pdf_path=None,
                skipped=False,
                skip_reason=None,
                log="\n".join(logs).strip() or "pandoc failed",
                work_dir=root,
            )

        tectonic = tools["tectonic"]
        assert tectonic is not None
        # Default tectonic mode forbids shell escape; keep compiling from isolated dir only.
        tectonic_cmd = [
            tectonic,
            "--keep-logs",
            "--outdir",
            str(root),
            str(tex_path),
        ]
        try:
            tex_proc = subprocess.run(
                tectonic_cmd,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CompileResult(
                pdf_path=None,
                skipped=False,
                skip_reason=None,
                log="\n".join(logs + [f"tectonic failed: {exc}"]).strip(),
                work_dir=root,
            )
        logs.append(tex_proc.stdout or "")
        logs.append(tex_proc.stderr or "")
        pdf_path = root / "main.pdf"
        if tex_proc.returncode != 0 or not pdf_path.is_file():
            return CompileResult(
                pdf_path=None,
                skipped=False,
                skip_reason=None,
                log="\n".join(logs).strip() or "tectonic failed",
                work_dir=root,
            )
        return CompileResult(
            pdf_path=pdf_path,
            skipped=False,
            skip_reason=None,
            log="\n".join(logs).strip(),
            work_dir=root,
        )
