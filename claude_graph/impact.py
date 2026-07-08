"""Blast-radius / impact-radius analysis: given a set of changed files,
find every caller, importer, and test that could be affected. Backs the
MCP `get_impact_radius_tool`."""

from __future__ import annotations

from claude_graph.graph_store import GraphStore

DEFAULT_DEPTH = 2


def get_impact_radius(store: GraphStore, changed_files: list[str], depth: int = DEFAULT_DEPTH) -> dict:
    seed_ids = _node_ids_for_files(store, changed_files)
    module_ids = {
        store.find_module_node(f)["id"] for f in changed_files if store.find_module_node(f) is not None
    }

    callers = _bfs_reverse(store, seed_ids, "calls", depth)
    importers = _bfs_reverse(store, module_ids, "imports", depth=1)
    tests = _tests_for_seeds(store, seed_ids)

    return {
        "changed_files": changed_files,
        "callers": callers,
        "importers": importers,
        "tests": tests,
    }


def _node_ids_for_files(store: GraphStore, files: list[str]) -> set[int]:
    ids: set[int] = set()
    for file in files:
        for node in store.nodes_for_file(file):
            ids.add(node["id"])
    return ids


def _bfs_reverse(store: GraphStore, seed_ids: set[int], kind: str, depth: int) -> list[dict]:
    """Walk edges of `kind` backwards from `seed_ids` up to `depth` hops,
    returning every node reached with the hop distance at which it was
    first found."""
    visited: dict[int, int] = {}
    frontier = set(seed_ids)
    current_depth = 0
    while frontier and current_depth < depth:
        current_depth += 1
        next_frontier: set[int] = set()
        for node_id in frontier:
            for edge in store.edges_by_dst(node_id, kind):
                if edge["src"] not in visited and edge["src"] not in seed_ids:
                    visited[edge["src"]] = current_depth
                    next_frontier.add(edge["src"])
        frontier = next_frontier

    results = []
    for node_id, hop in visited.items():
        node = store.get_node(node_id)
        if node is not None:
            results.append({"file": node["file"], "name": node["name"], "kind": node["kind"], "depth": hop})
    return results


def _tests_for_seeds(store: GraphStore, seed_ids: set[int]) -> list[dict]:
    results = []
    seen_files: set[str] = set()
    for node_id in seed_ids:
        for edge in store.edges_by_dst(node_id, "tests_for"):
            src = store.get_node(edge["src"])
            if src is not None and src["file"] not in seen_files:
                seen_files.add(src["file"])
                results.append({"file": src["file"]})
    return results
