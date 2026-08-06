"""CLI smoke tests."""

from __future__ import annotations

from pathlib import Path

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


def test_project_create_and_backup(tmp_path: Path, capsys) -> None:
    dest = tmp_path / "cli.deeprhetor"
    assert (
        main(
            [
                "project",
                "create",
                "--path",
                str(dest),
                "--title",
                "CLI",
                "--prompt",
                "hello",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert Path(out[0]) == dest
    assert dest.is_file()

    backup = tmp_path / "cli-backup.deeprhetor"
    assert main(["project", "backup", "--path", str(dest), "--dest", str(backup)]) == 0
    assert backup.is_file()
