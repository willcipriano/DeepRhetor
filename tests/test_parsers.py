"""Parser fixture tests for each local document format."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeprhetor.domain.sources import RawDocument
from deeprhetor.plugins.parsers import ParserRegistry, guess_media_type


@pytest.fixture
def fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "tests" / "fixtures" / "documents"


@pytest.fixture
def parsers() -> ParserRegistry:
    return ParserRegistry()


@pytest.mark.asyncio
async def test_parse_plaintext(fixtures_dir: Path, parsers: ParserRegistry) -> None:
    path = fixtures_dir / "sample.txt"
    raw = RawDocument(
        content=path.read_bytes(),
        media_type=guess_media_type(path.name),
        filename=path.name,
        title="sample",
    )
    parsed = await parsers.parse(raw)
    assert parsed.parser == "plaintext"
    assert len(parsed.segments) >= 2
    assert parsed.segments[0].status == "pending"
    assert parsed.segments[0].char_start == 0
    assert parsed.segments[0].char_end > 0
    assert "plain text" in parsed.text.lower()


@pytest.mark.asyncio
async def test_parse_markdown(fixtures_dir: Path, parsers: ParserRegistry) -> None:
    path = fixtures_dir / "sample.md"
    raw = RawDocument(
        content=path.read_bytes(),
        media_type=guess_media_type(path.name),
        filename=path.name,
    )
    parsed = await parsers.parse(raw)
    assert parsed.parser == "markdown"
    assert parsed.title == "Sample Title" or any(
        s.section_path == "Sample Title" for s in parsed.segments
    )
    assert any(s.section_path == "Section Two" for s in parsed.segments)
    assert all(s.status == "pending" for s in parsed.segments)


@pytest.mark.asyncio
async def test_parse_html(fixtures_dir: Path, parsers: ParserRegistry) -> None:
    path = fixtures_dir / "sample.html"
    raw = RawDocument(
        content=path.read_bytes(),
        media_type=guess_media_type(path.name),
        filename=path.name,
    )
    parsed = await parsers.parse(raw)
    assert parsed.parser == "html"
    assert "research" in parsed.text.lower() or "alpha" in parsed.text.lower()
    assert parsed.segments
    assert parsed.segments[0].char_start is not None


@pytest.mark.asyncio
async def test_parse_pdf(fixtures_dir: Path, parsers: ParserRegistry) -> None:
    path = fixtures_dir / "sample.pdf"
    raw = RawDocument(
        content=path.read_bytes(),
        media_type=guess_media_type(path.name),
        filename=path.name,
    )
    parsed = await parsers.parse(raw)
    assert parsed.parser == "pymupdf"
    assert any(s.page == 1 for s in parsed.segments)
    assert "PDF fixture" in parsed.text


@pytest.mark.asyncio
async def test_parse_docx(fixtures_dir: Path, parsers: ParserRegistry) -> None:
    path = fixtures_dir / "sample.docx"
    raw = RawDocument(
        content=path.read_bytes(),
        media_type=guess_media_type(path.name),
        filename=path.name,
    )
    parsed = await parsers.parse(raw)
    assert parsed.parser == "python-docx"
    assert any(s.section_path == "DOCX Fixture" for s in parsed.segments)
    assert "First DOCX paragraph" in parsed.text
