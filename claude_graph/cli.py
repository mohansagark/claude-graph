"""Command-line interface for claude-graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_graph.build import build_graph, update_graph
from claude_graph.graph_store import GraphStore
from claude_graph.install import install_claude_code
from claude_graph.repo import NotAGitRepoError, find_repo_root


def _resolve_repo_root(repo_arg: str | None) -> Path:
    start = Path(repo_arg).resolve() if repo_arg else Path.cwd()
    try:
        return find_repo_root(start)
    except NotAGitRepoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_build(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    stats = build_graph(repo_root, full_rebuild=True)
    print(json.dumps(stats, indent=2))


def _cmd_update(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    stats = update_graph(repo_root)
    print(json.dumps(stats, indent=2))


def _cmd_status(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    db_path = repo_root / ".claude-graph" / "graph.db"
    if not db_path.exists():
        print("No graph found. Run 'claude-graph build' first.")
        return
    with GraphStore(db_path) as store:
        print(json.dumps(store.stats(), indent=2))


def _cmd_install(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    result = install_claude_code(repo_root)
    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("build", _cmd_build),
        ("update", _cmd_update),
        ("status", _cmd_status),
        ("install", _cmd_install),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=None, help="Repository root (default: current directory)")
        sub.set_defaults(func=handler)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
