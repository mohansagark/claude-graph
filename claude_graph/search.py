"""Keyword search over function/class names and signatures via SQLite
FTS5. No embeddings, no vectors, no network calls — Claude Code (already
the LLM in the loop) does semantic ranking over these candidates itself.
Falls back to a plain LIKE query if the local sqlite3 build lacks FTS5,
so search never hard-fails a build on an unusual Python/sqlite3 build."""

from __future__ import annotations

from claude_graph.graph_store import GraphStore


def search_nodes(store: GraphStore, query: str, limit: int = 20) -> list[dict]:
    if not store.fts_enabled:
        return _fallback_like_search(store, query, limit)

    rows = store.conn.execute(
        """
        SELECT nodes.file, nodes.kind, nodes.name, nodes.start_line
        FROM nodes_fts
        JOIN nodes ON nodes.id = nodes_fts.rowid
        WHERE nodes_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    return [
        {"file": r["file"], "kind": r["kind"], "name": r["name"], "line": r["start_line"]} for r in rows
    ]


def _fallback_like_search(store: GraphStore, query: str, limit: int) -> list[dict]:
    rows = store.conn.execute(
        """
        SELECT file, kind, name, start_line FROM nodes
        WHERE name LIKE ? OR signature LIKE ?
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    return [
        {"file": r["file"], "kind": r["kind"], "name": r["name"], "line": r["start_line"]} for r in rows
    ]
