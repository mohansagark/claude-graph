"""Build and incrementally update the claude-graph knowledge graph.

Node IDs are stable across updates (nodes are keyed by (file, kind,
name), see GraphStore.sync_file_nodes), so editing a function's body
doesn't break edges that other files hold pointing at it. Only deleting
a function/class removes its node (and edges touching it).

Building/updating happens in three passes so cross-file edges resolve
correctly regardless of which file is processed first. Language support
is determined once, up front, so files with no LanguageConfig (README,
JSON, etc.) never enter any pass and never get a module node:

1. Ensure every to-be-processed file with a supported language has a
   module node (a placeholder for brand-new files), so pass 3's lookups
   always find a target to point at.
2. Parse each changed/new file and sync its module/function/class nodes.
3. Resolve calls, imports, and tests_for edges for each changed/new
   file, now that every node those edges might reference exists.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from claude_graph.graph_store import GraphStore
from claude_graph.languages import LanguageConfig, language_for_extension, load_language_config
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

            changed_or_new = []
            for path in tracked_files:
                try:
                    current_hash = _file_hash(repo_root / path)
                except OSError as exc:
                    # Unreadable tracked file (e.g. a broken symlink or a
                    # permission-restricted file): skip it for this
                    # update, same as pass 2 of _sync_files does for a
                    # file that fails mid-parse. Any existing DB entry for
                    # this path is left untouched, not crashed on.
                    print(f"warning: skipping {path}: {exc}", file=sys.stderr)
                    continue
                if store.get_file_hash(path) != current_hash:
                    changed_or_new.append(path)
            _sync_files(store, repo_root, tracked_files, changed_or_new)
        return store.stats()


def _sync_files(
    store: GraphStore, repo_root: Path, all_tracked: list[str], to_process: list[str]
) -> None:
    all_files = set(all_tracked)
    configs = load_language_config(repo_root)

    # Determine language support up front, before creating any nodes.
    # Files with no LanguageConfig (README.md, JSON, etc.) are skipped in
    # pass 2 below and never get a `files` row, so if pass 1 gave them a
    # placeholder module node anyway it would become a permanent ghost
    # node — invisible to update_graph's deleted-file cleanup, which only
    # looks at store.all_file_paths() (backed by the `files` table).
    supported: list[tuple[str, LanguageConfig]] = []
    for rel_path in to_process:
        config = language_for_extension(configs, rel_path)
        if config is not None:
            supported.append((rel_path, config))

    # Pass 1: guarantee a module node exists for every supported file
    # about to be processed, so pass 3's cross-file lookups never miss a
    # new file.
    for rel_path, _config in supported:
        if store.find_module_node(rel_path) is None:
            store.sync_file_nodes(rel_path, [("module", rel_path, 1, 1, "")])

    # Pass 2: parse each file and sync its own nodes.
    parsed: dict[str, tuple[dict[tuple[str, str], int], list[ParsedCall], list[ParsedImport]]] = {}
    for rel_path, config in supported:
        abs_path = repo_root / rel_path
        try:
            nodes, calls, imports = parse_file(abs_path, config)
        except OSError as exc:
            print(f"warning: skipping {rel_path}: {exc}", file=sys.stderr)
            # If this file never had a `files` row, it's genuinely new
            # (not previously readable/parsed), so pass 1's placeholder
            # module node above is a ghost with no chance of ever being
            # backed by real data — remove it now rather than leave it
            # invisible to update_graph's deleted-file cleanup (which
            # only looks at store.all_file_paths(), backed by `files`).
            # A file that *did* have a prior `files` row (readable once,
            # unreadable now) keeps its old data untouched instead of
            # being wiped by a transient read failure.
            if store.get_file_hash(rel_path) is None:
                store.clear_file(rel_path)
            continue

        node_specs = [("module", rel_path, 1, max((n.end_line for n in nodes), default=1), "")]
        node_specs += [(n.kind, n.name, n.start_line, n.end_line, n.signature) for n in nodes]
        name_to_id = store.sync_file_nodes(rel_path, node_specs)
        store.upsert_file(rel_path, _file_hash(abs_path), config.name)
        parsed[rel_path] = (name_to_id, calls, imports)

    # Pass 3: resolve calls/imports/tests_for now that every node they
    # might reference exists.
    for rel_path, (name_to_id, calls, imports) in parsed.items():
        module_id = name_to_id[("module", rel_path)]
        # Clear outgoing `calls` edges using every node id currently on
        # this file (not just the ones in the freshly built name map) so
        # a stale edge from a node that isn't in this parse's name_to_id
        # for any reason can't survive re-parse.
        current_node_ids = [row["id"] for row in store.nodes_for_file(rel_path)]

        store.clear_outgoing_edges(current_node_ids, "calls")
        for call in calls:
            if call.caller_name:
                # A named caller must resolve strictly against a
                # function node — a class that happens to share the
                # caller's bare name must never become the call's src.
                caller_id = name_to_id.get(("function", call.caller_name))
            else:
                caller_id = module_id
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


def _resolve_call_targets(
    store: GraphStore, name_to_id: dict[tuple[str, str], int], called_name: str
) -> list[int]:
    """Prefer a same-file function, then a same-file class (a bare-name
    call to a class is instantiation, so the class node is the correct
    target); otherwise resolve by name across the whole graph with the
    same function-before-class preference. The global fallback is a
    deliberate heuristic — multiple files with a same-named function
    will all be flagged as callees, trading precision for not missing a
    real caller (see README)."""
    local_function_id = name_to_id.get(("function", called_name))
    if local_function_id is not None:
        return [local_function_id]
    local_class_id = name_to_id.get(("class", called_name))
    if local_class_id is not None:
        return [local_class_id]

    global_functions = store.find_nodes_by_name(called_name, kind="function")
    if global_functions:
        return [row["id"] for row in global_functions]
    global_classes = store.find_nodes_by_name(called_name, kind="class")
    return [row["id"] for row in global_classes]
