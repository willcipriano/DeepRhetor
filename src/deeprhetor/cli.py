"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from deeprhetor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deeprhetor", description="DeepRhetor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print package version")

    serve = sub.add_parser("serve", help="Start the local web application (stub)")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (loopback default)")
    serve.add_argument("--port", type=int, default=8765, help="Bind port")

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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
