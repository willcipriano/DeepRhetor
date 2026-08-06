"""CLI smoke tests."""

from __future__ import annotations

from deeprhetor import __version__
from deeprhetor.cli import main


def test_version_command(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_serve_stub(capsys) -> None:
    assert main(["serve", "--port", "9000"]) == 0
    err = capsys.readouterr().err
    assert "not implemented" in err
    assert "9000" in err
