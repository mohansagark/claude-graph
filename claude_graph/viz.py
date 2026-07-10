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
    targets = list(store.find_nodes_by_name(symbol, kind="function")) + list(
        store.find_nodes_by_name(symbol, kind="class")
    )
    if not targets:
        return [], [], []

    node_rows = {row["id"]: row for row in targets}
    edges: list[dict] = []

    for target in targets:
        for edge in store.edges_by_dst(target["id"], "calls"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": target["id"], "kind": "calls"})
        for edge in store.edges_by_src(target["id"], "calls"):
            dst = store.get_node(edge["dst"])
            if dst is not None:
                node_rows[dst["id"]] = dst
                edges.append({"source": target["id"], "target": dst["id"], "kind": "calls"})
        module = store.find_module_node(target["file"])
        if module is not None:
            node_rows[module["id"]] = module
            for edge in store.edges_by_src(module["id"], "imports"):
                dst = store.get_node(edge["dst"])
                if dst is not None:
                    node_rows[dst["id"]] = dst
                    edges.append({"source": module["id"], "target": dst["id"], "kind": "imports"})

    nodes = [_node_dict(row) for row in node_rows.values()]
    highlight_ids = [t["id"] for t in targets]
    return nodes, edges, highlight_ids


def _impact_neighborhood(
    store: GraphStore, changed_files: list[str], depth: int
) -> tuple[list[dict], list[dict], list[int]]:
    seed_rows = {}
    for file in changed_files:
        for row in store.nodes_for_file(file):
            seed_rows[row["id"]] = row
    if not seed_rows:
        return [], [], []

    node_rows = dict(seed_rows)
    edges: list[dict] = []

    frontier = set(seed_rows.keys())
    seen = set(frontier)
    for _ in range(depth):
        next_frontier: set[int] = set()
        for node_id in frontier:
            for edge in store.edges_by_dst(node_id, "calls"):
                src = store.get_node(edge["src"])
                if src is not None:
                    node_rows[src["id"]] = src
                    edges.append({"source": src["id"], "target": node_id, "kind": "calls"})
                    if src["id"] not in seen:
                        seen.add(src["id"])
                        next_frontier.add(src["id"])
        frontier = next_frontier
        if not frontier:
            break

    for file in changed_files:
        module = store.find_module_node(file)
        if module is None:
            continue
        node_rows[module["id"]] = module
        for edge in store.edges_by_dst(module["id"], "imports"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": module["id"], "kind": "imports"})

    for node_id in list(seed_rows.keys()):
        for edge in store.edges_by_dst(node_id, "tests_for"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": node_id, "kind": "tests_for"})

    nodes = [_node_dict(row) for row in node_rows.values()]
    highlight_ids = list(seed_rows.keys())
    return nodes, edges, highlight_ids


def _render_html(payload: dict) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    d3_script = _D3_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(payload).replace("</", "<\\/")
    html = template.replace("{{D3_SCRIPT}}", d3_script)
    html = html.replace("{{DATA_JSON}}", data_json)
    return html
