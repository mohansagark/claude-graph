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
from claude_graph.viz import render_graph


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


def _cmd_serve(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    from claude_graph.mcp_server import serve

    serve(repo_root)


def _cmd_viz(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    db_path = repo_root / ".claude-graph" / "graph.db"
    output_path = Path(args.output) if args.output else repo_root / ".claude-graph" / "graph.html"

    if args.symbol:
        scope, symbol, changed_files = "symbol", args.symbol, None
    elif args.impact:
        scope, symbol, changed_files = "impact", None, args.impact
    else:
        scope, symbol, changed_files = "full", None, None

    with GraphStore(db_path) as store:
        result = render_graph(
            store, output_path, scope=scope, symbol=symbol, changed_files=changed_files, depth=args.depth
        )
    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("build", _cmd_build),
        ("update", _cmd_update),
        ("status", _cmd_status),
        ("install", _cmd_install),
        ("serve", _cmd_serve),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=None, help="Repository root (default: current directory)")
        sub.set_defaults(func=handler)

    viz_parser = subparsers.add_parser("viz")
    viz_parser.add_argument("--repo", default=None, help="Repository root (default: current directory)")
    viz_parser.add_argument(
        "-o", "--output", default=None, help="Output HTML path (default: .claude-graph/graph.html)"
    )
    viz_parser.add_argument("--depth", type=int, default=2, help="Impact BFS depth (only used with --impact)")
    viz_group = viz_parser.add_mutually_exclusive_group()
    viz_group.add_argument("--symbol", default=None, help="Render the neighborhood of this function/class name")
    viz_group.add_argument(
        "--impact", nargs="+", default=None, help="Render the impact radius of these changed files"
    )
    viz_parser.set_defaults(func=_cmd_viz)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
