"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from deeprhetor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deeprhetor", description="DeepRhetor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print package version")

    serve = sub.add_parser("serve", help="Start the local web application (stub)")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (loopback default)")
    serve.add_argument("--port", type=int, default=8765, help="Bind port")

    project = sub.add_parser("project", help="Manage portable project SQLite files")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    create = project_sub.add_parser(
        "create",
        help="Create a new .deeprhetor (or .sqlite) project file",
    )
    create.add_argument(
        "--path",
        required=True,
        help="Destination path (prefer .deeprhetor or .sqlite)",
    )
    create.add_argument("--title", required=True, help="Project title")
    create.add_argument("--prompt", required=True, help="Authoritative research prompt")
    create.add_argument(
        "--config-json",
        default="{}",
        help="JSON object stored as the initial configuration snapshot",
    )

    backup = project_sub.add_parser(
        "backup",
        help="Checkpoint WAL and copy a project file safely",
    )
    backup.add_argument("--path", required=True, help="Source project path")
    backup.add_argument("--dest", required=True, help="Destination backup path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "serve":
        print(
            f"deeprhetor serve is not implemented yet "
            f"(would bind {args.host}:{args.port}).",
            file=sys.stderr,
        )
        return 0

    if args.command == "project":
        return _handle_project(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def _handle_project(args: argparse.Namespace) -> int:
    from deeprhetor.services.project_store import backup_project, create_project

    if args.project_command == "create":
        try:
            config = json.loads(args.config_json)
        except json.JSONDecodeError as exc:
            print(f"invalid --config-json: {exc}", file=sys.stderr)
            return 2
        if not isinstance(config, dict):
            print("--config-json must be a JSON object", file=sys.stderr)
            return 2
        try:
            opened = create_project(
                args.path,
                title=args.title,
                prompt=args.prompt,
                config_snapshot=config,
            )
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(opened.path)
        print(opened.project.id)
        return 0

    if args.project_command == "backup":
        try:
            dest = backup_project(args.path, args.dest)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(Path(dest))
        return 0

    print(f"unknown project command: {args.project_command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
