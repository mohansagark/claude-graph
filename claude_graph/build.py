"""Build and incrementally update the claude-graph knowledge graph.

Node IDs are stable across updates (nodes are keyed by (file, kind,
name), see GraphStore.sync_file_nodes), so editing a function's body
doesn't break edges that other files hold pointing at it. Only deleting
a function/class removes its node (and edges touching it).

Building/updating happens in three passes so cross-file edges resolve
correctly regardless of which file is processed first:

1. Ensure every file about to be (re)processed has a module node (a
   placeholder for brand-new files), so pass 3's lookups always find a
   target to point at.
2. Parse each changed/new file and sync its module/function/class nodes.
3. Resolve calls, imports, and tests_for edges for each changed/new
   file, now that every node those edges might reference exists.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from claude_graph.graph_store import GraphStore
from claude_graph.languages import language_for_extension, load_language_config
from claude_graph.parser import ParsedCall, ParsedImport, find_tested_file, parse_file, resolve_import
from claude_graph.repo import list_tracked_files


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _graph_db_path(repo_root: Path) -> Path:
    return repo_root / ".claude-graph" / "graph.db"


def build_graph(repo_root: Path, full_rebuild: bool = True) -> dict:
    """Full build: parse every git-tracked file and (re)write the graph."""
    db_path = _graph_db_path(repo_root)
    if full_rebuild and db_path.exists():
        db_path.unlink()

    with GraphStore(db_path) as store:
        with store.transaction():
            tracked_files = list_tracked_files(repo_root)
            _sync_files(store, repo_root, tracked_files, tracked_files)
        return store.stats()


def update_graph(repo_root: Path) -> dict:
    """Incremental update: re-parse only files whose content hash changed
    since the last build, and drop files that were deleted."""
    db_path = _graph_db_path(repo_root)
    if not db_path.exists():
        return build_graph(repo_root, full_rebuild=True)

    with GraphStore(db_path) as store:
        with store.transaction():
            tracked_files = list_tracked_files(repo_root)
            all_files = set(tracked_files)
            known_files = set(store.all_file_paths())

            for deleted in known_files - all_files:
                store.clear_file(deleted)

            changed_or_new = [
                path
                for path in tracked_files
                if store.get_file_hash(path) != _file_hash(repo_root / path)
            ]
            _sync_files(store, repo_root, tracked_files, changed_or_new)
        return store.stats()


def _sync_files(
    store: GraphStore, repo_root: Path, all_tracked: list[str], to_process: list[str]
) -> None:
    all_files = set(all_tracked)
    configs = load_language_config(repo_root)

    # Pass 1: guarantee a module node exists for every file about to be
    # processed, so pass 3's cross-file lookups never miss a new file.
    for rel_path in to_process:
        if store.find_module_node(rel_path) is None:
            store.sync_file_nodes(rel_path, [("module", rel_path, 1, 1, "")])

    # Pass 2: parse each file and sync its own nodes.
    parsed: dict[str, tuple[dict[str, int], list[ParsedCall], list[ParsedImport]]] = {}
    for rel_path in to_process:
        config = language_for_extension(configs, rel_path)
        if config is None:
            continue
        abs_path = repo_root / rel_path
        try:
            nodes, calls, imports = parse_file(abs_path, config)
        except OSError as exc:
            print(f"warning: skipping {rel_path}: {exc}", file=sys.stderr)
            continue

        node_specs = [("module", rel_path, 1, max((n.end_line for n in nodes), default=1), "")]
        node_specs += [(n.kind, n.name, n.start_line, n.end_line, n.signature) for n in nodes]
        name_to_id = store.sync_file_nodes(rel_path, node_specs)
        store.upsert_file(rel_path, _file_hash(abs_path), config.name)
        parsed[rel_path] = (name_to_id, calls, imports)

    # Pass 3: resolve calls/imports/tests_for now that every node they
    # might reference exists.
    for rel_path, (name_to_id, calls, imports) in parsed.items():
        module_id = name_to_id[rel_path]
        all_node_ids = list(name_to_id.values())

        store.clear_outgoing_edges(all_node_ids, "calls")
        for call in calls:
            caller_id = name_to_id.get(call.caller_name) if call.caller_name else module_id
            if caller_id is None:
                continue
            for callee_id in _resolve_call_targets(store, name_to_id, call.called_name):
                store.add_edge(caller_id, callee_id, "calls")

        store.clear_outgoing_edges([module_id], "imports")
        for imp in imports:
            target_file = resolve_import(rel_path, imp.module_text, all_files)
            if target_file is None:
                continue
            target_module = store.find_module_node(target_file)
            if target_module is not None:
                store.add_edge(module_id, target_module["id"], "imports")

        store.clear_outgoing_edges([module_id], "tests_for")
        tested_file = find_tested_file(rel_path, all_files)
        if tested_file is not None:
            tested_module = store.find_module_node(tested_file)
            if tested_module is not None:
                store.add_edge(module_id, tested_module["id"], "tests_for")


def _resolve_call_targets(store: GraphStore, name_to_id: dict[str, int], called_name: str) -> list[int]:
    """Prefer a same-file match; otherwise resolve by name across the
    whole graph. The global fallback is a deliberate heuristic — two
    files with a same-named function will both be flagged as callees,
    trading precision for not missing a real caller (see README)."""
    if called_name in name_to_id:
        return [name_to_id[called_name]]
    return [row["id"] for row in store.find_nodes_by_name(called_name, kind="function")]
