"""CLI smoke tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from deeprhetor import __version__
from deeprhetor.cli import build_parser, main


def test_version_command(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_serve_defaults_to_loopback() -> None:
    args = build_parser().parse_args(["serve", "--port", "9000"])
    assert args.host == "127.0.0.1"
    assert args.port == 9000


def test_serve_starts_uvicorn(tmp_path: Path, capsys) -> None:
    projects = tmp_path / "projects"

    def fake_run(app, host, port, log_level="info"):
        assert host == "127.0.0.1"
        assert port == 9001
        assert app is not None

    with patch("uvicorn.run", side_effect=fake_run):
        assert main(["serve", "--port", "9001", "--projects-dir", str(projects)]) == 0
    out = capsys.readouterr().out
    assert "127.0.0.1:9001" in out


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
