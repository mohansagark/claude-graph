"""SQLite-backed graph storage for claude-graph.

Nodes are keyed by (file, kind, name) via a UNIQUE constraint, so a
node's id stays stable across incremental updates as long as its file,
kind, and name don't change — editing a function's body doesn't
invalidate edges other files hold pointing at it. Only deleting a
function/class actually removes its node (see clear_file for whole-file
deletion, sync_file_nodes for per-file reconciliation).
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    language TEXT NOT NULL,
    last_parsed REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    UNIQUE(file, kind, name)
);

CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src INTEGER NOT NULL,
    dst INTEGER NOT NULL,
    kind TEXT NOT NULL,
    FOREIGN KEY(src) REFERENCES nodes(id),
    FOREIGN KEY(dst) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src, kind);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, signature, content='nodes', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, name, signature) VALUES (new.id, new.name, new.signature);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, name, signature) VALUES('delete', old.id, old.name, old.signature);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, name, signature) VALUES('delete', old.id, old.name, old.signature);
  INSERT INTO nodes_fts(rowid, name, signature) VALUES (new.id, new.name, new.signature);
END;
"""


class GraphStore:
    """Wraps one `.claude-graph/graph.db` SQLite file."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.fts_enabled = self._try_enable_fts()

    def _try_enable_fts(self) -> bool:
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            return False

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _delete_nodes_and_their_edges(self, node_ids: list[int]) -> None:
        """Delete nodes and all edges touching them. No-op on empty list."""
        if not node_ids:
            return
        placeholders = ",".join("?" for _ in node_ids)
        self.conn.execute(
            f"DELETE FROM edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
            (*node_ids, *node_ids),
        )
        self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", node_ids)

    # -- files ----------------------------------------------------------

    def upsert_file(self, path: str, file_hash: str, language: str) -> None:
        self.conn.execute(
            """
            INSERT INTO files (path, hash, language, last_parsed)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                hash = excluded.hash,
                language = excluded.language,
                last_parsed = excluded.last_parsed
            """,
            (path, file_hash, language, time.time()),
        )

    def get_file_hash(self, path: str) -> str | None:
        row = self.conn.execute("SELECT hash FROM files WHERE path = ?", (path,)).fetchone()
        return row["hash"] if row else None

    def get_file_row(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    def all_file_paths(self) -> list[str]:
        return [row["path"] for row in self.conn.execute("SELECT path FROM files").fetchall()]

    def clear_file(self, path: str) -> None:
        """Remove a file's row and all nodes/edges rooted at its nodes.
        Used for files deleted from the repo, not for normal re-indexing
        (see sync_file_nodes, which preserves node ids across edits)."""
        node_ids = [
            row["id"] for row in self.conn.execute("SELECT id FROM nodes WHERE file = ?", (path,)).fetchall()
        ]
        self._delete_nodes_and_their_edges(node_ids)
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))

    # -- nodes ------------------------------------------------------------

    def add_node(
        self, file: str, kind: str, name: str, start_line: int, end_line: int, signature: str = ""
    ) -> int:
        """Raw insert, for direct tests and for one-off nodes. Build/update
        code should use sync_file_nodes instead, which preserves ids
        across re-parses of the same file."""
        cursor = self.conn.execute(
            "INSERT INTO nodes (file, kind, name, start_line, end_line, signature) VALUES (?, ?, ?, ?, ?, ?)",
            (file, kind, name, start_line, end_line, signature),
        )
        return cursor.lastrowid

    def sync_file_nodes(
        self, file: str, node_specs: list[tuple[str, str, int, int, str]]
    ) -> dict[tuple[str, str], int]:
        """Upserts each (kind, name, start_line, end_line, signature) into
        `nodes` keyed by (file, kind, name), preserving the row's id
        across calls. Any existing node for `file` whose (kind, name) is
        not in `node_specs` is deleted, along with edges touching it.
        Returns (kind, name) -> id for every node now on `file`. Keying by
        (kind, name) rather than name alone avoids collapsing a function
        and a class that share a bare name in the same file into one map
        entry (bare-name collisions across files/kinds are still an
        accepted design tradeoff — see README — but same-file (kind,
        name) collisions must not corrupt edge resolution)."""
        existing = {
            (row["kind"], row["name"]): row["id"]
            for row in self.conn.execute(
                "SELECT id, kind, name FROM nodes WHERE file = ?", (file,)
            ).fetchall()
        }
        wanted_keys = {(kind, name) for kind, name, *_ in node_specs}

        stale_ids = [node_id for key, node_id in existing.items() if key not in wanted_keys]
        self._delete_nodes_and_their_edges(stale_ids)

        name_to_id: dict[tuple[str, str], int] = {}
        for kind, name, start_line, end_line, signature in node_specs:
            row = self.conn.execute(
                """
                INSERT INTO nodes (file, kind, name, start_line, end_line, signature)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file, kind, name) DO UPDATE SET
                    start_line = excluded.start_line,
                    end_line = excluded.end_line,
                    signature = excluded.signature
                RETURNING id
                """,
                (file, kind, name, start_line, end_line, signature),
            ).fetchone()
            name_to_id[(kind, name)] = row["id"]
        return name_to_id

    def get_node(self, node_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()

    def find_nodes_by_name(self, name: str, kind: str | None = None) -> list[sqlite3.Row]:
        if kind is None:
            return self.conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM nodes WHERE name = ? AND kind = ?", (name, kind)
        ).fetchall()

    def find_module_node(self, file: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM nodes WHERE file = ? AND kind = 'module'", (file,)
        ).fetchone()

    def nodes_for_file(self, file: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM nodes WHERE file = ? ORDER BY start_line", (file,)
        ).fetchall()

    def all_nodes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()

    # -- edges ------------------------------------------------------------

    def add_edge(self, src: int, dst: int, kind: str) -> None:
        self.conn.execute("INSERT INTO edges (src, dst, kind) VALUES (?, ?, ?)", (src, dst, kind))

    def clear_outgoing_edges(self, node_ids: list[int], kind: str) -> None:
        if not node_ids:
            return
        placeholders = ",".join("?" for _ in node_ids)
        self.conn.execute(
            f"DELETE FROM edges WHERE src IN ({placeholders}) AND kind = ?", (*node_ids, kind)
        )

    def edges_by_dst(self, node_id: int, kind: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edges WHERE dst = ? AND kind = ?", (node_id, kind)
        ).fetchall()

    def edges_by_src(self, node_id: int, kind: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edges WHERE src = ? AND kind = ?", (node_id, kind)
        ).fetchall()

    def all_edges(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM edges ORDER BY id").fetchall()

    # -- stats ------------------------------------------------------------

    def stats(self) -> dict:
        node_count = self.conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]
        edge_count = self.conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
        file_count = self.conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        languages = [
            row["language"]
            for row in self.conn.execute("SELECT DISTINCT language FROM files").fetchall()
        ]
        return {
            "files": file_count,
            "nodes": node_count,
            "edges": edge_count,
            "languages": sorted(languages),
        }
