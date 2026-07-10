"""Static, self-contained HTML graph visualization: renders the graph (or
a scoped neighborhood of it) to a single local HTML file with a vendored
D3 force-directed layout. No server, no network — the output is opened
directly via file://."""

from __future__ import annotations

import json
from pathlib import Path

from claude_graph.graph_store import GraphStore

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_PATH = _STATIC_DIR / "graph_template.html"
_D3_PATH = _STATIC_DIR / "d3.v7.min.js"

_VALID_SCOPES = {"full", "symbol", "impact"}


def render_graph(
    store: GraphStore,
    output_path: Path,
    scope: str = "full",
    symbol: str | None = None,
    changed_files: list[str] | None = None,
    depth: int = 2,
) -> dict:
    if scope not in _VALID_SCOPES:
        raise ValueError(f"unknown scope: {scope!r}, must be one of {sorted(_VALID_SCOPES)}")
    if scope == "symbol" and not symbol:
        raise ValueError("scope='symbol' requires a symbol name")
    if scope == "impact" and not changed_files:
        raise ValueError("scope='impact' requires changed_files")

    if scope == "full":
        nodes, edges, highlight_ids = _full_graph(store)
    elif scope == "symbol":
        nodes, edges, highlight_ids = _symbol_neighborhood(store, symbol)
    else:
        nodes, edges, highlight_ids = _impact_neighborhood(store, changed_files, depth)

    payload = {"nodes": nodes, "edges": edges, "highlight_ids": highlight_ids}
    html = _render_html(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    return {"path": str(output_path), "node_count": len(nodes), "edge_count": len(edges)}


def _node_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "file": row["file"], "line": row["start_line"]}


def _full_graph(store: GraphStore) -> tuple[list[dict], list[dict], list[int]]:
    nodes = [_node_dict(row) for row in store.all_nodes()]
    edges = [{"source": row["src"], "target": row["dst"], "kind": row["kind"]} for row in store.all_edges()]
    return nodes, edges, []


def _symbol_neighborhood(store: GraphStore, symbol: str) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3


def _impact_neighborhood(
    store: GraphStore, changed_files: list[str], depth: int
) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3


def _render_html(payload: dict) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    d3_script = _D3_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(payload).replace("</", "<\\/")
    html = template.replace("{{D3_SCRIPT}}", d3_script)
    html = html.replace("{{DATA_JSON}}", data_json)
    return html
