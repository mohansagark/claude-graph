"""Structural graph queries: callers, callees, imports, tests, and
per-file summaries. These back the MCP `query_graph_tool`."""

from __future__ import annotations

from claude_graph.graph_store import GraphStore


def callers_of(store: GraphStore, name: str) -> list[dict]:
    """Every function that calls a function named `name`, anywhere in
    the graph. Matches are by name, a heuristic across files with
    same-named functions (see README limitations)."""
    results = []
    for target in store.find_nodes_by_name(name, kind="function"):
        for edge in store.edges_by_dst(target["id"], "calls"):
            src = store.get_node(edge["src"])
            if src is not None:
                results.append(
                    {"file": src["file"], "name": src["name"], "kind": src["kind"], "line": src["start_line"]}
                )
    return results


def callees_of(store: GraphStore, name: str) -> list[dict]:
    """Every function/class called by a function named `name`."""
    results = []
    for source in store.find_nodes_by_name(name, kind="function"):
        for edge in store.edges_by_src(source["id"], "calls"):
            dst = store.get_node(edge["dst"])
            if dst is not None:
                results.append(
                    {"file": dst["file"], "name": dst["name"], "kind": dst["kind"], "line": dst["start_line"]}
                )
    return results


def imports_of(store: GraphStore, file: str) -> list[str]:
    """Files that `file` imports, resolved to tracked file paths."""
    module = store.find_module_node(file)
    if module is None:
        return []
    results = []
    for edge in store.edges_by_src(module["id"], "imports"):
        dst = store.get_node(edge["dst"])
        if dst is not None:
            results.append(dst["file"])
    return results


def tests_for(store: GraphStore, file: str) -> list[str]:
    """Test files linked to `file` by naming convention."""
    module = store.find_module_node(file)
    if module is None:
        return []
    results = []
    for edge in store.edges_by_dst(module["id"], "tests_for"):
        src = store.get_node(edge["src"])
        if src is not None:
            results.append(src["file"])
    return results


def file_summary(store: GraphStore, file: str) -> dict | None:
    """Language, node list, and last-parsed time for one file."""
    file_row = store.get_file_row(file)
    if file_row is None:
        return None
    nodes = [
        {"kind": n["kind"], "name": n["name"], "start_line": n["start_line"], "end_line": n["end_line"]}
        for n in store.nodes_for_file(file)
        if n["kind"] != "module"
    ]
    return {
        "file": file,
        "language": file_row["language"],
        "last_parsed": file_row["last_parsed"],
        "nodes": nodes,
    }


def query_graph(store: GraphStore, pattern: str, target: str):
    """Dispatch for the MCP `query_graph_tool`'s `pattern` argument."""
    if pattern == "callers_of":
        return callers_of(store, target)
    if pattern == "callees_of":
        return callees_of(store, target)
    if pattern == "imports_of":
        return imports_of(store, target)
    if pattern == "tests_for":
        return tests_for(store, target)
    if pattern == "file_summary":
        return file_summary(store, target)
    raise ValueError(f"unknown query pattern: {pattern}")
