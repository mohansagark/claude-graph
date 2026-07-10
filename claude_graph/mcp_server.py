"""MCP server exposing claude-graph's query/impact/search/build tools to
Claude Code over stdio. No HTTP transport, no network listener."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from claude_graph.build import build_graph, update_graph
from claude_graph.graph_store import GraphStore
from claude_graph.impact import get_impact_radius
from claude_graph.query import query_graph
from claude_graph.search import search_nodes
from claude_graph.viz import render_graph


def _db_path(repo_root: Path) -> Path:
    return repo_root / ".claude-graph" / "graph.db"


def create_server(repo_root: Path) -> FastMCP:
    app = FastMCP("claude-graph")

    @app.tool()
    def build_or_update_graph() -> dict:
        """Build the graph if none exists, or incrementally update it."""
        if _db_path(repo_root).exists():
            return update_graph(repo_root)
        return build_graph(repo_root, full_rebuild=True)

    @app.tool()
    def get_graph_stats() -> dict:
        """Node/edge/file counts and languages detected."""
        db_path = _db_path(repo_root)
        if not db_path.exists():
            return {"files": 0, "nodes": 0, "edges": 0, "languages": []}
        with GraphStore(db_path) as store:
            return store.stats()

    @app.tool()
    def query_graph_tool(pattern: str, target: str) -> dict:
        """Structural query. `pattern` is one of: callers_of, callees_of,
        imports_of, tests_for, file_summary. `target` is a function/class
        name for callers_of/callees_of, or a file path for the others."""
        with GraphStore(_db_path(repo_root)) as store:
            result = query_graph(store, pattern, target)
        return {"pattern": pattern, "target": target, "result": result}

    @app.tool()
    def get_impact_radius_tool(changed_files: list[str], depth: int = 2) -> dict:
        """Blast radius of the given changed file paths: callers,
        importers, and tests that could be affected. `depth` controls how
        many hops of the `calls` graph to walk backwards from the changed
        files when collecting callers (default 2)."""
        with GraphStore(_db_path(repo_root)) as store:
            return get_impact_radius(store, changed_files, depth=depth)

    @app.tool()
    def search_nodes_tool(query: str) -> list[dict]:
        """Keyword search over function/class names and signatures."""
        with GraphStore(_db_path(repo_root)) as store:
            return search_nodes(store, query)

    @app.tool()
    def render_graph_tool(
        scope: str = "full",
        symbol: str | None = None,
        changed_files: list[str] | None = None,
        depth: int = 2,
    ) -> dict:
        """Render the graph (or a scoped neighborhood) to a self-contained,
        offline HTML file at .claude-graph/graph.html. `scope` is one of:
        full (default, whole graph), symbol (neighborhood of `symbol`), or
        impact (impact radius of `changed_files` at `depth`). Returns the
        file path — tell the user to open it in a browser; this tool does
        not open it for them."""
        output_path = repo_root / ".claude-graph" / "graph.html"
        with GraphStore(_db_path(repo_root)) as store:
            return render_graph(
                store, output_path, scope=scope, symbol=symbol, changed_files=changed_files, depth=depth
            )

    return app


def serve(repo_root: Path) -> None:
    app = create_server(repo_root)
    app.run(transport="stdio")
