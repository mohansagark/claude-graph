"""Keyword search over function/class names and signatures via SQLite
FTS5. No embeddings, no vectors, no network calls — Claude Code (already
the LLM in the loop) does semantic ranking over these candidates itself.
Falls back to a plain LIKE query if the local sqlite3 build lacks FTS5,
so search never hard-fails a build on an unusual Python/sqlite3 build."""

from __future__ import annotations

from claude_graph.graph_store import GraphStore


def search_nodes(store: GraphStore, query: str, limit: int = 20) -> list[dict]:
    # Empty or whitespace-only queries return empty results
    if not query.strip():
        return []

    if not store.fts_enabled:
        return _fallback_like_search(store, query, limit)

    # Sanitize FTS5 query: convert tokens to quoted phrases for robustness
    sanitized_query = _sanitize_fts_query(query)

    rows = store.conn.execute(
        """
        SELECT nodes.file, nodes.kind, nodes.name, nodes.start_line
        FROM nodes_fts
        JOIN nodes ON nodes.id = nodes_fts.rowid
        WHERE nodes_fts MATCH ? AND nodes.kind != 'module'
        ORDER BY rank
        LIMIT ?
        """,
        (sanitized_query, limit),
    ).fetchall()
    return [
        {"file": r["file"], "kind": r["kind"], "name": r["name"], "line": r["start_line"]} for r in rows
    ]


def _sanitize_fts_query(query: str) -> str:
    """Sanitize query into FTS5 phrase tokens.

    Split on whitespace, escape internal quotes, and wrap each token in double quotes.
    This prevents FTS5 syntax errors from special characters like hyphens, parentheses, etc.

    Example: "foo-bar baz" → '"foo-bar" "baz"'
    """
    tokens = query.split()
    quoted_tokens = []
    for token in tokens:
        # Double any internal quotes
        escaped_token = token.replace('"', '""')
        quoted_tokens.append(f'"{escaped_token}"')
    return " ".join(quoted_tokens)


def _fallback_like_search(store: GraphStore, query: str, limit: int) -> list[dict]:
    """Fall back to LIKE search when FTS5 is not available.

    Escapes wildcard characters (%, _) and backslash to ensure literal matching.
    """
    # Escape backslash first, then %, then _
    escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped_query}%"

    rows = store.conn.execute(
        """
        SELECT file, kind, name, start_line FROM nodes
        WHERE (name LIKE ? ESCAPE '\\' OR signature LIKE ? ESCAPE '\\') AND kind != 'module'
        ORDER BY name
        LIMIT ?
        """,
        (pattern, pattern, limit),
    ).fetchall()
    return [
        {"file": r["file"], "kind": r["kind"], "name": r["name"], "line": r["start_line"]} for r in rows
    ]
