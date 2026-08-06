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

    serve = sub.add_parser("serve", help="Start the local web application on loopback")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (loopback default)")
    serve.add_argument("--port", type=int, default=8765, help="Bind port")
    serve.add_argument(
        "--projects-dir",
        default=None,
        help="Directory for .deeprhetor project files (default: ./projects)",
    )

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

    run = sub.add_parser(
        "run",
        help="Run a live end-to-end research report (OpenRouter + search providers)",
    )
    run.add_argument("--prompt", required=True, help="Authoritative research prompt")
    run.add_argument("--title", default=None, help="Project title (defaults to prompt)")
    run.add_argument(
        "--path",
        default=None,
        help="Project .deeprhetor path (default: ./projects/<slug>.deeprhetor)",
    )
    run.add_argument(
        "--export-dir",
        default=None,
        help="Directory for .tex/.bib/manifest exports",
    )
    run.add_argument(
        "--auto-approve",
        action="store_true",
        default=True,
        help="Auto-approve the generated research plan (default)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "serve":
        return _handle_serve(args)

    if args.command == "project":
        return _handle_project(args)

    if args.command == "run":
        return _handle_run(args)

    parser.error(f"unknown command: {args.command}")
    return 2


def _handle_run(args: argparse.Namespace) -> int:
    import asyncio
    import re

    from deeprhetor.services.e2e_run import run_end_to_end

    prompt = args.prompt
    title = args.title or prompt[:80]
    if args.path:
        path = Path(args.path)
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "project"
        path = Path.cwd() / "projects" / f"{slug}.deeprhetor"
    path.parent.mkdir(parents=True, exist_ok=True)
    export_dir = Path(args.export_dir) if args.export_dir else path.with_suffix("") / "exports"

    print(f"DeepRhetor live run", flush=True)
    print(f"  prompt: {prompt}", flush=True)
    print(f"  project: {path}", flush=True)
    print(f"  export: {export_dir}", flush=True)

    try:
        result = asyncio.run(
            run_end_to_end(
                prompt=prompt,
                title=title,
                project_path=path,
                export_dir=export_dir,
                auto_approve=True,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"run_id: {result.run_id}", flush=True)
    print(f"documents: {result.documents}", flush=True)
    print(f"approved_claims: {result.approved_claims}", flush=True)
    print(f"publication: {result.publication_status}", flush=True)
    if result.supervisor_fallback:
        print(f"supervisor_fallback: yes ({result.supervisor_error})", flush=True)
    for worker in result.worker_summaries:
        print(
            f"  worker {worker.get('provider')}: "
            f"hits={worker.get('hits')} archived={len(worker.get('archived_document_ids') or [])}",
            flush=True,
        )
    if result.tex_path:
        print(f"tex: {result.tex_path}", flush=True)
    if result.bib_path:
        print(f"bib: {result.bib_path}", flush=True)
    if result.manifest_path:
        print(f"manifest: {result.manifest_path}", flush=True)
    if result.pdf_path:
        print(f"pdf: {result.pdf_path}", flush=True)
    else:
        print("pdf: (not compiled — install pandoc + tectonic for PDF)", flush=True)
    return 0 if result.approved_claims > 0 and result.tex_path else 1


def _handle_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from deeprhetor.web import create_app

    host = args.host or "127.0.0.1"
    projects_dir = Path(args.projects_dir) if args.projects_dir else Path.cwd() / "projects"
    app = create_app(projects_dir=projects_dir)
    print(f"DeepRhetor listening on http://{host}:{args.port}", flush=True)
    print(f"Projects directory: {projects_dir.resolve()}", flush=True)
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


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
