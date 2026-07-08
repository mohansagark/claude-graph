# claude-graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `claude-graph`, a local-only knowledge graph tool for Claude Code on macOS: parse a git repo with Tree-sitter, store a structural graph (functions, classes, calls, imports, test coverage) in SQLite, and expose it over MCP (stdio) so Claude Code can answer "what calls this", "what breaks if I change this file", and "is this tested" without reading the whole repo.

**Architecture:** Three layers in one Python package (`claude_graph/`): a Tree-sitter parser driven by a config-driven `languages.toml`, a SQLite graph store with stable node IDs across incremental re-parses, and a stdio-only MCP server exposing five tools. A CLI (`claude-graph`) wraps build/update/status/install/serve. No hooks, no embeddings, no other-editor support, no home-directory writes — see Global Constraints.

**Tech Stack:** Python 3.11+, `tree-sitter` + `tree-sitter-language-pack`, `mcp` (official SDK, `FastMCP`), stdlib `sqlite3` (FTS5) and `tomllib`, `pytest`.

## Global Constraints

- macOS, Claude Code only. No support for Cursor/Windsurf/Zed/Copilot/etc.
- **Zero network calls, ever**, during normal operation (build/update/query/impact/search/serve). Proven by an automated test with outbound sockets disabled (Task 13), not just documented.
- No auto-update hooks. Updates are manual/on-demand only (`claude-graph update` or the `build_or_update_graph` MCP tool), never triggered by file-save or bash-call events.
- No embeddings, no vector search, no semantic-search API calls of any kind. Keyword/FTS5 search returns candidates; Claude Code (already the LLM in the loop) does semantic reasoning over them.
- No home-directory writes. Everything this tool writes lives inside the target repo: `.claude-graph/graph.db`, `.mcp.json`, `.claude/skills/`.
- One graph per repo. No multi-repo registry, no daemon, no watch mode.
- Python 3.11+ (uses stdlib `tomllib`, no extra TOML dependency).
- Bundled default language config covers Python and JavaScript/TypeScript/TSX only. More languages can be added per-repo via `.claude-graph/languages.toml` without code changes.
- Project lives at `~/Documents/projects/claude/claude-graph`, package name `claude-graph`, not published to public PyPI — teammates install via `git clone` + `pip install -e .`.
- Node IDs in the graph store are stable across incremental updates (keyed by `(file, kind, name)`) so editing a function's body never invalidates edges other files hold pointing at it. Only deleting a function/class removes its node and the edges touching it.

---

### Task 1: Project scaffolding + repo root/file-listing module

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md` (minimal placeholder; fully written in Task 13)
- Create: `claude_graph/__init__.py`
- Create: `claude_graph/repo.py`
- Test: `tests/conftest.py`
- Test: `tests/test_repo.py`

**Interfaces:**
- Produces: `claude_graph.repo.NotAGitRepoError` (exception class); `find_repo_root(start: Path) -> Path`; `list_tracked_files(repo_root: Path) -> list[str]`.

- [ ] **Step 1: Write pyproject.toml, .gitignore, minimal README, and package init**

`pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claude-graph"
version = "0.1.0"
description = "Local-only knowledge graph for Claude Code on macOS"
readme = {file = "README.md", content-type = "text/markdown"}
requires-python = ">=3.11"
authors = [{ name = "Mohansagar" }]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
dependencies = [
    "mcp>=1.0.0,<2",
    "tree-sitter>=0.23.0,<1",
    "tree-sitter-language-pack>=0.3.0,<1",
]

[project.scripts]
claude-graph = "claude_graph.cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9",
]

[tool.hatch.build.targets.wheel]
packages = ["claude_graph"]

[tool.hatch.build.targets.wheel.force-include]
"claude_graph/default_languages.toml" = "claude_graph/default_languages.toml"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.claude-graph/
*.egg-info/
dist/
build/
.pytest_cache/
```

`README.md`:
```markdown
# claude-graph

Local-only knowledge graph for Claude Code on macOS. See implementation
plan in `docs/superpowers/` for design details; full usage docs land in
Task 13 of that plan.
```

`claude_graph/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 2: Write the failing test for repo.py**

`tests/conftest.py`:
```python
"""Shared pytest fixtures for claude-graph tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """An empty git repo rooted at tmp_path."""
    _git("init", "-q", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def commit_files():
    """Call with a repo root to stage all files for `git ls-files`
    (no commit needed — `git ls-files` reads the index)."""

    def _stage(repo_root: Path) -> None:
        _git("add", "-A", cwd=repo_root)

    return _stage
```

`tests/test_repo.py`:
```python
import subprocess
from pathlib import Path

import pytest

from claude_graph.repo import NotAGitRepoError, find_repo_root, list_tracked_files


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_find_repo_root_from_nested_dir(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path.resolve()


def test_find_repo_root_raises_outside_git(tmp_path):
    with pytest.raises(NotAGitRepoError):
        find_repo_root(tmp_path)


def test_list_tracked_files(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "b.py").write_text("print(2)\n")
    _git("add", "-A", cwd=tmp_path)
    assert sorted(list_tracked_files(tmp_path)) == ["a.py", "b.py"]


def test_list_tracked_files_respects_ignore_file(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "generated.py").write_text("print(2)\n")
    (tmp_path / ".claude-graphignore").write_text("generated.py\n")
    _git("add", "-A", cwd=tmp_path)
    assert sorted(list_tracked_files(tmp_path)) == [".claude-graphignore", "a.py"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_repo.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'claude_graph.repo'`

- [ ] **Step 4: Write repo.py**

```python
"""Repository root discovery and tracked-file listing for claude-graph."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


class NotAGitRepoError(Exception):
    """Raised when a directory is not inside a git repository."""


def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` to find the nearest directory containing
    `.git`. Raises NotAGitRepoError if none is found."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise NotAGitRepoError(
        f"{start} is not inside a git repository (no .git directory found)"
    )


def list_tracked_files(repo_root: Path) -> list[str]:
    """Git-tracked file paths relative to `repo_root`, filtered by
    `.claude-graphignore` patterns if present."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [line for line in result.stdout.splitlines() if line]
    patterns = _load_ignore_patterns(repo_root)
    if not patterns:
        return files
    return [f for f in files if not _is_ignored(f, patterns)]


def _load_ignore_patterns(repo_root: Path) -> list[str]:
    ignore_file = repo_root / ".claude-graphignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _is_ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_repo.py -v`
Expected: 4 passed

- [ ] **Step 6: Verify the package installs cleanly end-to-end**

Run: `pip install -e ".[dev]"`
Expected: installs `claude-graph`, `mcp`, `tree-sitter`, `tree-sitter-language-pack`, `pytest` with no errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore README.md claude_graph/__init__.py claude_graph/repo.py tests/conftest.py tests/test_repo.py
git commit -m "Scaffold project and add repo-root/tracked-file discovery"
```

---

### Task 2: Graph store (SQLite schema, stable-ID upsert, FTS5)

**Files:**
- Create: `claude_graph/graph_store.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GraphStore(db_path: Path)` with methods used by every later task: `upsert_file`, `get_file_hash`, `get_file_row`, `all_file_paths`, `clear_file`, `add_node`, `sync_file_nodes`, `get_node`, `find_nodes_by_name`, `find_module_node`, `nodes_for_file`, `add_edge`, `clear_outgoing_edges`, `edges_by_dst`, `edges_by_src`, `stats`, `transaction()` (context manager), `fts_enabled: bool`, `.conn` (raw `sqlite3.Connection`), `.close()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_graph_store.py`:
```python
from claude_graph.graph_store import GraphStore


def test_add_and_get_node(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    node_id = store.add_node("foo.py", "function", "foo", 1, 3, "def foo():")
    store.conn.commit()
    row = store.get_node(node_id)
    assert row["name"] == "foo"
    assert row["file"] == "foo.py"
    store.close()


def test_add_edge_and_query_by_dst(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    caller = store.add_node("a.py", "function", "caller", 1, 2, "")
    callee = store.add_node("b.py", "function", "callee", 1, 2, "")
    store.add_edge(caller, callee, "calls")
    store.conn.commit()
    edges = store.edges_by_dst(callee, "calls")
    assert len(edges) == 1
    assert edges[0]["src"] == caller
    store.close()


def test_clear_file_removes_nodes_and_edges(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.upsert_file("a.py", "hash1", "python")
    caller = store.add_node("a.py", "function", "caller", 1, 2, "")
    callee = store.add_node("b.py", "function", "callee", 1, 2, "")
    store.add_edge(caller, callee, "calls")
    store.conn.commit()

    store.clear_file("a.py")
    store.conn.commit()

    assert store.get_node(caller) is None
    assert store.get_file_hash("a.py") is None
    assert store.edges_by_dst(callee, "calls") == []
    store.close()


def test_transaction_rolls_back_on_error(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    try:
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO nodes (file, kind, name, start_line, end_line) VALUES (?,?,?,?,?)",
                ("a.py", "function", "x", 1, 2),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.find_nodes_by_name("x") == []
    store.close()


def test_search_fts_finds_matching_node(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file(path)")
    store.conn.commit()
    if not store.fts_enabled:
        store.close()
        return
    rows = store.conn.execute(
        "SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH ?", ("parse",)
    ).fetchall()
    assert len(rows) == 1
    store.close()


def test_stats(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.upsert_file("a.py", "hash1", "python")
    store.add_node("a.py", "module", "a.py", 1, 10, "")
    store.conn.commit()
    stats = store.stats()
    assert stats["files"] == 1
    assert stats["nodes"] == 1
    assert stats["languages"] == ["python"]
    store.close()


def test_sync_file_nodes_preserves_id_on_update(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    ids1 = store.sync_file_nodes("a.py", [("function", "foo", 1, 2, "def foo():")])
    store.conn.commit()
    ids2 = store.sync_file_nodes("a.py", [("function", "foo", 1, 3, "def foo(x):")])
    store.conn.commit()
    assert ids1["foo"] == ids2["foo"]
    row = store.get_node(ids2["foo"])
    assert row["end_line"] == 3
    assert row["signature"] == "def foo(x):"
    store.close()


def test_sync_file_nodes_removes_stale_nodes_and_their_edges(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    ids1 = store.sync_file_nodes(
        "a.py", [("function", "foo", 1, 2, ""), ("function", "bar", 3, 4, "")]
    )
    other = store.add_node("b.py", "function", "caller", 1, 2, "")
    store.add_edge(other, ids1["bar"], "calls")
    store.conn.commit()

    ids2 = store.sync_file_nodes("a.py", [("function", "foo", 1, 2, "")])  # bar removed
    store.conn.commit()

    assert "bar" not in ids2
    assert store.edges_by_dst(ids1["bar"], "calls") == []
    store.close()


def test_clear_outgoing_edges_only_removes_edges_sourced_from_given_nodes(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    a = store.add_node("a.py", "function", "a", 1, 2, "")
    b = store.add_node("b.py", "function", "b", 1, 2, "")
    c = store.add_node("c.py", "function", "c", 1, 2, "")
    store.add_edge(a, b, "calls")
    store.add_edge(c, a, "calls")  # incoming edge into a, must survive
    store.conn.commit()

    store.clear_outgoing_edges([a], "calls")
    store.conn.commit()

    assert store.edges_by_src(a, "calls") == []
    assert len(store.edges_by_dst(a, "calls")) == 1
    store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_graph_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.graph_store'`

- [ ] **Step 3: Write graph_store.py**

```python
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
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            self.conn.execute(
                f"DELETE FROM edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
                (*node_ids, *node_ids),
            )
            self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", node_ids)
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
    ) -> dict[str, int]:
        """Upserts each (kind, name, start_line, end_line, signature) into
        `nodes` keyed by (file, kind, name), preserving the row's id
        across calls. Any existing node for `file` whose (kind, name) is
        not in `node_specs` is deleted, along with edges touching it.
        Returns name -> id for every node now on `file`."""
        existing = {
            (row["kind"], row["name"]): row["id"]
            for row in self.conn.execute(
                "SELECT id, kind, name FROM nodes WHERE file = ?", (file,)
            ).fetchall()
        }
        wanted_keys = {(kind, name) for kind, name, *_ in node_specs}

        stale_ids = [node_id for key, node_id in existing.items() if key not in wanted_keys]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            self.conn.execute(
                f"DELETE FROM edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
                (*stale_ids, *stale_ids),
            )
            self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", stale_ids)

        name_to_id: dict[str, int] = {}
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
            name_to_id[name] = row["id"]
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_graph_store.py -v`
Expected: 9 passed (the FTS test passes trivially if `fts_enabled` is False on this machine — it was confirmed True in manual verification, so it will exercise the real path)

- [ ] **Step 5: Commit**

```bash
git add claude_graph/graph_store.py tests/test_graph_store.py
git commit -m "Add SQLite graph store with stable-id upsert and FTS5 search"
```

---

### Task 3: Language config module

**Files:**
- Create: `claude_graph/languages.py`
- Create: `claude_graph/default_languages.toml`
- Test: `tests/test_languages.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `LanguageConfig` dataclass (`name`, `extensions`, `grammar`, `function_node_types`, `class_node_types`, `import_node_types`, `call_node_types` — all `tuple[str, ...]` except `name`/`grammar` which are `str`); `load_default_languages() -> dict[str, LanguageConfig]`; `load_language_config(repo_root: Path) -> dict[str, LanguageConfig]`; `language_for_extension(configs: dict[str, LanguageConfig], file_path: str) -> LanguageConfig | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_languages.py`:
```python
from claude_graph.languages import (
    language_for_extension,
    load_default_languages,
    load_language_config,
)


def test_default_languages_include_python_and_typescript():
    configs = load_default_languages()
    assert "python" in configs
    assert ".py" in configs["python"].extensions
    assert configs["python"].grammar == "python"
    assert "typescript" in configs
    assert ".ts" in configs["typescript"].extensions


def test_language_for_extension_matches_py():
    configs = load_default_languages()
    config = language_for_extension(configs, "src/foo.py")
    assert config is not None
    assert config.name == "python"


def test_language_for_extension_returns_none_for_unknown():
    configs = load_default_languages()
    assert language_for_extension(configs, "foo.rs") is None


def test_repo_override_extends_defaults(tmp_path):
    (tmp_path / ".claude-graph").mkdir()
    (tmp_path / ".claude-graph" / "languages.toml").write_text(
        """
[languages.ruby]
extensions = [".rb"]
grammar = "ruby"
function_node_types = ["method"]
class_node_types = ["class"]
import_node_types = ["call"]
call_node_types = ["call"]
"""
    )
    configs = load_language_config(tmp_path)
    assert "ruby" in configs
    assert "python" in configs  # bundled default still present


def test_repo_override_replaces_matching_language(tmp_path):
    (tmp_path / ".claude-graph").mkdir()
    (tmp_path / ".claude-graph" / "languages.toml").write_text(
        """
[languages.python]
extensions = [".py", ".pyi"]
grammar = "python"
function_node_types = ["function_definition"]
class_node_types = ["class_definition"]
import_node_types = ["import_statement"]
call_node_types = ["call"]
"""
    )
    configs = load_language_config(tmp_path)
    assert ".pyi" in configs["python"].extensions
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_languages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.languages'`

- [ ] **Step 3: Write default_languages.toml and languages.py**

`claude_graph/default_languages.toml`:
```toml
[languages.python]
extensions = [".py"]
grammar = "python"
function_node_types = ["function_definition"]
class_node_types = ["class_definition"]
import_node_types = ["import_statement", "import_from_statement"]
call_node_types = ["call"]

[languages.typescript]
extensions = [".ts"]
grammar = "typescript"
function_node_types = ["function_declaration", "method_definition"]
class_node_types = ["class_declaration"]
import_node_types = ["import_statement"]
call_node_types = ["call_expression"]

[languages.tsx]
extensions = [".tsx"]
grammar = "tsx"
function_node_types = ["function_declaration", "method_definition"]
class_node_types = ["class_declaration"]
import_node_types = ["import_statement"]
call_node_types = ["call_expression"]

[languages.javascript]
extensions = [".js", ".jsx", ".mjs", ".cjs"]
grammar = "javascript"
function_node_types = ["function_declaration", "method_definition"]
class_node_types = ["class_declaration"]
import_node_types = ["import_statement"]
call_node_types = ["call_expression"]
```

`claude_graph/languages.py`:
```python
"""Language configuration loading for claude-graph.

Languages are described declaratively (extensions, tree-sitter grammar
name, and the node types that count as a function/class/import/call for
that grammar) so adding a new language is a TOML edit, not a code change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class LanguageConfig:
    name: str
    extensions: tuple[str, ...]
    grammar: str
    function_node_types: tuple[str, ...]
    class_node_types: tuple[str, ...]
    import_node_types: tuple[str, ...]
    call_node_types: tuple[str, ...]


def _parse_languages_toml(data: dict) -> dict[str, LanguageConfig]:
    configs: dict[str, LanguageConfig] = {}
    for name, entry in data.get("languages", {}).items():
        configs[name] = LanguageConfig(
            name=name,
            extensions=tuple(entry["extensions"]),
            grammar=entry["grammar"],
            function_node_types=tuple(entry.get("function_node_types", [])),
            class_node_types=tuple(entry.get("class_node_types", [])),
            import_node_types=tuple(entry.get("import_node_types", [])),
            call_node_types=tuple(entry.get("call_node_types", [])),
        )
    return configs


def load_default_languages() -> dict[str, LanguageConfig]:
    toml_text = (
        resources.files("claude_graph")
        .joinpath("default_languages.toml")
        .read_text(encoding="utf-8")
    )
    return _parse_languages_toml(tomllib.loads(toml_text))


def load_language_config(repo_root: Path) -> dict[str, LanguageConfig]:
    """Bundled defaults, extended/overridden by
    `<repo_root>/.claude-graph/languages.toml` if present. A repo override
    for a language name replaces that language's entry entirely; other
    bundled languages are untouched."""
    configs = dict(load_default_languages())
    override_path = repo_root / ".claude-graph" / "languages.toml"
    if override_path.exists():
        override_data = tomllib.loads(override_path.read_text(encoding="utf-8"))
        configs.update(_parse_languages_toml(override_data))
    return configs


def language_for_extension(
    configs: dict[str, LanguageConfig], file_path: str
) -> LanguageConfig | None:
    suffix = Path(file_path).suffix
    for config in configs.values():
        if suffix in config.extensions:
            return config
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_languages.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/languages.py claude_graph/default_languages.toml tests/test_languages.py
git commit -m "Add config-driven language definitions (Python, JS/TS/TSX)"
```

---

### Task 4: Parser — node extraction

**Files:**
- Create: `claude_graph/parser.py`
- Create: `tests/fixtures/sample.py`
- Create: `tests/fixtures/sample.ts`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: `claude_graph.languages.LanguageConfig`.
- Produces: `ParsedNode` dataclass (`kind`, `name`, `start_line`, `end_line`, `signature`); `ParsedCall` dataclass (`caller_name: str | None`, `called_name: str`); `ParsedImport` dataclass (`module_text: str`); `parse_file(path: Path, config: LanguageConfig) -> tuple[list[ParsedNode], list[ParsedCall], list[ParsedImport]]`.

- [ ] **Step 1: Write fixtures and the failing tests**

`tests/fixtures/sample.py`:
```python
def helper(x):
    return x + 1


def main(y):
    return helper(y)


class Greeter:
    def greet(self, name):
        return f"hello {name}"
```

`tests/fixtures/sample.ts`:
```typescript
function helper(x: number): number {
  return x + 1;
}

function main(y: number): number {
  return helper(y);
}

class Greeter {
  greet(name: string): string {
    return `hello ${name}`;
  }
}
```

`tests/test_parser.py`:
```python
from pathlib import Path

from claude_graph.languages import language_for_extension, load_default_languages
from claude_graph.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_python_extracts_functions_and_classes():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    nodes, calls, imports = parse_file(FIXTURES / "sample.py", config)

    names = {(n.kind, n.name) for n in nodes}
    assert ("function", "helper") in names
    assert ("function", "main") in names
    assert ("class", "Greeter") in names
    assert ("function", "greet") in names  # method counts as function


def test_parse_typescript_extracts_functions_and_classes():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.ts")
    nodes, calls, imports = parse_file(FIXTURES / "sample.ts", config)

    names = {(n.kind, n.name) for n in nodes}
    assert ("function", "helper") in names
    assert ("function", "main") in names
    assert ("class", "Greeter") in names
    assert ("function", "greet") in names


def test_parse_python_signature_is_readable():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    nodes, _, _ = parse_file(FIXTURES / "sample.py", config)
    main_node = next(n for n in nodes if n.name == "main")
    assert main_node.signature == "def main(y)"


def test_parse_python_captures_calls_with_enclosing_function():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    _, calls, _ = parse_file(FIXTURES / "sample.py", config)
    assert any(c.caller_name == "main" and c.called_name == "helper" for c in calls)


def test_parse_empty_file_returns_empty_lists(tmp_path):
    configs = load_default_languages()
    config = language_for_extension(configs, "empty.py")
    empty = tmp_path / "empty.py"
    empty.write_text("")
    nodes, calls, imports = parse_file(empty, config)
    assert nodes == []
    assert calls == []
    assert imports == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.parser'`

- [ ] **Step 3: Write parser.py**

```python
"""Tree-sitter based parsing: extracts nodes (functions, classes) and
calls/imports from source files, using the node types declared in a
LanguageConfig so adding a language needs no code change here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node as TSNode
from tree_sitter_language_pack import get_parser

from claude_graph.languages import LanguageConfig


@dataclass
class ParsedNode:
    kind: str  # 'function' | 'class'
    name: str
    start_line: int
    end_line: int
    signature: str


@dataclass
class ParsedCall:
    caller_name: str | None  # None means the call happens at module scope
    called_name: str


@dataclass
class ParsedImport:
    module_text: str


def _find_all(node: TSNode, types: set[str]) -> list[TSNode]:
    matches = []
    if node.type in types:
        matches.append(node)
    for child in node.children:
        matches.extend(_find_all(child, types))
    return matches


def _signature(node: TSNode, source: bytes) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte:end].decode("utf-8", errors="replace")
    return text.strip().rstrip(":{").strip()


def _enclosing_function_name(call_node: TSNode, function_types: set[str]) -> str | None:
    """Walk up from a call node to find the innermost function/method
    that contains it, so a call can be attributed to its caller."""
    current = call_node.parent
    while current is not None:
        if current.type in function_types:
            name_node = current.child_by_field_name("name")
            return name_node.text.decode("utf-8") if name_node is not None else None
        current = current.parent
    return None


def _call_target_name(call_node: TSNode) -> str | None:
    function_field = call_node.child_by_field_name("function")
    if function_field is None:
        return None
    if function_field.type == "attribute":  # Python obj.method()
        attr = function_field.child_by_field_name("attribute")
        return attr.text.decode("utf-8") if attr is not None else None
    if function_field.type == "member_expression":  # JS/TS obj.method()
        prop = function_field.child_by_field_name("property")
        return prop.text.decode("utf-8") if prop is not None else None
    return function_field.text.decode("utf-8")


def parse_file(
    path: Path, config: LanguageConfig
) -> tuple[list[ParsedNode], list[ParsedCall], list[ParsedImport]]:
    """Parse one file per its LanguageConfig. Tree-sitter parsers are
    error-tolerant (they emit ERROR nodes rather than raising), so this
    only raises on OSError (e.g. a permission problem reading the file) —
    callers should catch that and skip the file, not the whole build."""
    source = path.read_bytes()
    parser = get_parser(config.grammar)
    tree = parser.parse(source)
    root = tree.root_node

    function_types = set(config.function_node_types)
    class_types = set(config.class_node_types)
    call_types = set(config.call_node_types)
    import_types = set(config.import_node_types)

    nodes: list[ParsedNode] = []
    for fn_node in _find_all(root, function_types):
        name_node = fn_node.child_by_field_name("name")
        if name_node is None:
            continue
        nodes.append(
            ParsedNode(
                kind="function",
                name=name_node.text.decode("utf-8"),
                start_line=fn_node.start_point[0] + 1,
                end_line=fn_node.end_point[0] + 1,
                signature=_signature(fn_node, source),
            )
        )

    for cls_node in _find_all(root, class_types):
        name_node = cls_node.child_by_field_name("name")
        if name_node is None:
            continue
        nodes.append(
            ParsedNode(
                kind="class",
                name=name_node.text.decode("utf-8"),
                start_line=cls_node.start_point[0] + 1,
                end_line=cls_node.end_point[0] + 1,
                signature=_signature(cls_node, source),
            )
        )

    calls: list[ParsedCall] = []
    for call_node in _find_all(root, call_types):
        called_name = _call_target_name(call_node)
        if called_name is None:
            continue
        caller_name = _enclosing_function_name(call_node, function_types)
        calls.append(ParsedCall(caller_name=caller_name, called_name=called_name))

    imports: list[ParsedImport] = []
    for import_node in _find_all(root, import_types):
        module_field = (
            import_node.child_by_field_name("module_name")
            or import_node.child_by_field_name("name")
            or import_node.child_by_field_name("source")
        )
        if module_field is not None:
            text = module_field.text.decode("utf-8").strip("'\"")
            imports.append(ParsedImport(module_text=text))

    return nodes, calls, imports
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/parser.py tests/fixtures/sample.py tests/fixtures/sample.ts tests/test_parser.py
git commit -m "Add Tree-sitter node/call/import extraction"
```

---

### Task 5: Parser — import resolution and tests_for naming convention

**Files:**
- Modify: `claude_graph/parser.py` (append `resolve_import` and `find_tested_file`)
- Modify: `tests/test_parser.py` (append tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: `resolve_import(importer_file: str, module_text: str, all_files: set[str]) -> str | None`; `find_tested_file(test_path: str, all_files: set[str]) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parser.py`:
```python
from claude_graph.parser import find_tested_file, resolve_import


def test_find_tested_file_python_prefix_convention():
    all_files = {"foo.py", "test_foo.py"}
    assert find_tested_file("test_foo.py", all_files) == "foo.py"


def test_find_tested_file_python_suffix_convention():
    all_files = {"foo.py", "foo_test.py"}
    assert find_tested_file("foo_test.py", all_files) == "foo.py"


def test_find_tested_file_js_spec_convention():
    all_files = {"foo.ts", "foo.spec.ts"}
    assert find_tested_file("foo.spec.ts", all_files) == "foo.ts"


def test_find_tested_file_returns_none_when_no_match():
    all_files = {"test_foo.py"}
    assert find_tested_file("test_foo.py", all_files) is None


def test_resolve_relative_import():
    all_files = {"src/foo.ts", "src/bar.ts"}
    assert resolve_import("src/bar.ts", "./foo", all_files) == "src/foo.ts"


def test_resolve_python_dotted_import():
    all_files = {"pkg/foo.py", "pkg/bar.py", "pkg/__init__.py"}
    assert resolve_import("pkg/bar.py", "pkg.foo", all_files) == "pkg/foo.py"


def test_resolve_import_returns_none_for_third_party():
    all_files = {"pkg/bar.py"}
    assert resolve_import("pkg/bar.py", "requests", all_files) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_tested_file'`

- [ ] **Step 3: Append resolve_import and find_tested_file to parser.py**

Append to `claude_graph/parser.py`:
```python
import os

_TEST_SUFFIXES = (".test", ".spec")


def find_tested_file(test_path: str, all_files: set[str]) -> str | None:
    """Best-effort match of a test file to the file it tests, by naming
    convention only (test_foo.py <-> foo.py, foo_test.py <-> foo.py,
    foo.spec.ts / foo.test.ts <-> foo.ts). Returns None if no convention
    matches or no candidate file exists."""
    p = Path(test_path)
    stem = p.stem
    directory = str(p.parent)

    candidate_stems: list[str] = []
    if stem.startswith("test_"):
        candidate_stems.append(stem[len("test_"):])
    if stem.endswith("_test"):
        candidate_stems.append(stem[: -len("_test")])
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix):
            candidate_stems.append(stem[: -len(suffix)])

    for candidate_stem in candidate_stems:
        for ext in (p.suffix, ".py", ".ts", ".tsx", ".js", ".jsx"):
            candidate_path = str(Path(directory) / f"{candidate_stem}{ext}")
            if candidate_path in all_files:
                return candidate_path
    return None


def resolve_import(importer_file: str, module_text: str, all_files: set[str]) -> str | None:
    """Best-effort resolution of an import's module text to a tracked
    file path, by trying common relative-path (JS/TS) and dotted-package
    (Python) conventions. Returns None rather than guessing when nothing
    tracked matches (e.g. a third-party package)."""
    importer_dir = Path(importer_file).parent

    if module_text.startswith("."):
        base = os.path.normpath(str(importer_dir / module_text))
        candidates = [f"{base}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".py")]
        candidates += [f"{base}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
    else:
        as_path = module_text.replace(".", "/")
        candidates = [
            f"{as_path}.py",
            f"{as_path}/__init__.py",
            f"{as_path}.ts",
            f"{as_path}.tsx",
            f"{as_path}.js",
        ]

    for candidate in candidates:
        if candidate in all_files:
            return candidate
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/parser.py tests/test_parser.py
git commit -m "Add import resolution and tests_for naming-convention matching"
```

---

### Task 6: Build orchestration (build_graph, update_graph)

**Files:**
- Create: `claude_graph/build.py`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `GraphStore`, `load_language_config`, `language_for_extension`, `parse_file`, `resolve_import`, `find_tested_file`, `list_tracked_files`.
- Produces: `build_graph(repo_root: Path, full_rebuild: bool = True) -> dict` (stats dict); `update_graph(repo_root: Path) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/test_build.py`:
```python
import subprocess
from pathlib import Path

from claude_graph.build import build_graph, update_graph
from claude_graph.graph_store import GraphStore


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_full_build_extracts_nodes_and_same_file_call_edge(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)

    stats = build_graph(tmp_path, full_rebuild=True)
    assert stats["files"] == 1
    assert stats["nodes"] == 3  # module + foo + bar

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        bar = store.find_nodes_by_name("bar", kind="function")[0]
        callers = store.edges_by_dst(bar["id"], "calls")
        assert len(callers) == 1


def test_cross_file_call_resolves_by_global_name(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "from b import helper\n\ndef main():\n    return helper()\n")
    _write(tmp_path, "b.py", "def helper():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)

    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        helper = store.find_nodes_by_name("helper", kind="function")[0]
        callers = store.edges_by_dst(helper["id"], "calls")
        assert len(callers) == 1
        caller_node = store.get_node(callers[0]["src"])
        assert caller_node["name"] == "main"


def test_import_resolution_links_module_nodes(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "pkg/a.py", "def foo():\n    return 1\n")
    _write(tmp_path, "pkg/b.py", "from pkg.a import foo\n")
    _write(tmp_path, "pkg/__init__.py", "")
    _git("add", "-A", cwd=tmp_path)

    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        module_a = store.find_module_node("pkg/a.py")
        importers = store.edges_by_dst(module_a["id"], "imports")
        assert len(importers) == 1


def test_tests_for_links_naming_convention(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "test_calc.py", "def test_add():\n    assert add(1, 2) == 3\n")
    _git("add", "-A", cwd=tmp_path)

    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        module_calc = store.find_module_node("calc.py")
        tests = store.edges_by_dst(module_calc["id"], "tests_for")
        assert len(tests) == 1


def test_incremental_update_skips_unchanged_files(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    _write(tmp_path, "b.py", "def bar():\n    return 2\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        last_parsed_before = store.get_file_row("b.py")["last_parsed"]

    _write(tmp_path, "a.py", "def foo():\n    return 2\n")
    _git("add", "-A", cwd=tmp_path)
    update_graph(tmp_path)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        last_parsed_after = store.get_file_row("b.py")["last_parsed"]
        assert last_parsed_before == last_parsed_after


def test_updating_function_body_preserves_id_so_callers_edge_survives(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    _write(tmp_path, "b.py", "from a import foo\n\ndef caller():\n    return foo()\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        foo_id_before = store.find_nodes_by_name("foo", kind="function")[0]["id"]
        assert len(store.edges_by_dst(foo_id_before, "calls")) == 1

    _write(tmp_path, "a.py", "def foo():\n    return 42\n")  # body changed, name unchanged
    _git("add", "-A", cwd=tmp_path)
    update_graph(tmp_path)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        foo_id_after = store.find_nodes_by_name("foo", kind="function")[0]["id"]
        assert foo_id_after == foo_id_before
        assert len(store.edges_by_dst(foo_id_after, "calls")) == 1


def test_update_removes_deleted_file(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    (tmp_path / "a.py").unlink()
    _git("add", "-A", cwd=tmp_path)
    stats = update_graph(tmp_path)

    assert stats["files"] == 0
    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        assert store.find_module_node("a.py") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.build'`

- [ ] **Step 3: Write build.py**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_build.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/build.py tests/test_build.py
git commit -m "Add build/update orchestration with stable cross-file edge resolution"
```

---

### Task 7: Query module

**Files:**
- Create: `claude_graph/query.py`
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `GraphStore`, `build_graph`.
- Produces: `callers_of(store, name) -> list[dict]`; `callees_of(store, name) -> list[dict]`; `imports_of(store, file) -> list[str]`; `tests_for(store, file) -> list[str]`; `file_summary(store, file) -> dict | None`; `query_graph(store, pattern: str, target: str) -> list | dict | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_query.py`:
```python
import subprocess
from pathlib import Path

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.query import callees_of, callers_of, file_summary, imports_of, query_graph
from claude_graph.query import tests_for as tests_for_query


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _build_sample_repo(tmp_path: Path) -> Path:
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "from b import helper\n\ndef main():\n    return helper()\n")
    _write(tmp_path, "b.py", "def helper():\n    return 1\n")
    _write(tmp_path, "test_b.py", "def test_helper():\n    assert helper() == 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    return tmp_path


def test_callers_of_finds_cross_file_caller(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        results = callers_of(store, "helper")
    assert any(r["file"] == "a.py" and r["name"] == "main" for r in results)


def test_callees_of_finds_called_function(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        results = callees_of(store, "main")
    assert any(r["file"] == "b.py" and r["name"] == "helper" for r in results)


def test_imports_of_finds_imported_file(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        results = imports_of(store, "a.py")
    assert results == ["b.py"]


def test_tests_for_finds_linked_test_file(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        results = tests_for_query(store, "b.py")
    assert results == ["test_b.py"]


def test_file_summary_lists_nodes(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        summary = file_summary(store, "b.py")
    assert summary["language"] == "python"
    assert any(n["name"] == "helper" for n in summary["nodes"])
    assert all(n["kind"] != "module" for n in summary["nodes"])


def test_file_summary_returns_none_for_unknown_file(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        assert file_summary(store, "nope.py") is None


def test_query_graph_dispatches_by_pattern(tmp_path):
    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        result = query_graph(store, "callers_of", "helper")
    assert any(r["name"] == "main" for r in result)


def test_query_graph_raises_on_unknown_pattern(tmp_path):
    import pytest

    repo = _build_sample_repo(tmp_path)
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        with pytest.raises(ValueError):
            query_graph(store, "not_a_real_pattern", "x")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.query'`

- [ ] **Step 3: Write query.py**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_query.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/query.py tests/test_query.py
git commit -m "Add structural query functions (callers_of, callees_of, imports_of, tests_for, file_summary)"
```

---

### Task 8: Impact radius (blast-radius analysis)

**Files:**
- Create: `claude_graph/impact.py`
- Test: `tests/test_impact.py`

**Interfaces:**
- Consumes: `GraphStore`, `build_graph`.
- Produces: `get_impact_radius(store: GraphStore, changed_files: list[str], depth: int = 2) -> dict` with keys `changed_files`, `callers` (`list[dict]` with `file`/`name`/`kind`/`depth`), `importers` (same shape), `tests` (`list[dict]` with `file`).

- [ ] **Step 1: Write the failing tests**

`tests/test_impact.py`:
```python
import subprocess
from pathlib import Path

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.impact import get_impact_radius


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_impact_radius_finds_direct_caller(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "from b import helper\n\ndef main():\n    return helper()\n")
    _write(tmp_path, "b.py", "def helper():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["b.py"])

    assert impact["changed_files"] == ["b.py"]
    assert any(c["file"] == "a.py" and c["name"] == "main" and c["depth"] == 1 for c in impact["callers"])


def test_impact_radius_finds_transitive_caller_within_depth(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def top():\n    return mid()\n\ndef mid():\n    return low()\n")
    _write(tmp_path, "b.py", "def low():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["b.py"], depth=2)

    names_by_depth = {c["name"]: c["depth"] for c in impact["callers"]}
    assert names_by_depth.get("mid") == 1
    assert names_by_depth.get("top") == 2


def test_impact_radius_finds_importer(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "from b import helper\n")
    _write(tmp_path, "b.py", "def helper():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["b.py"])

    assert any(i["file"] == "a.py" for i in impact["importers"])


def test_impact_radius_finds_test_file(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "test_calc.py", "def test_add():\n    assert add(1, 2) == 3\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["calc.py"])

    assert {"file": "test_calc.py"} in impact["tests"]


def test_impact_radius_for_unchanged_file_has_no_callers(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "solo.py", "def lonely():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["solo.py"])

    assert impact["callers"] == []
    assert impact["importers"] == []
    assert impact["tests"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_impact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.impact'`

- [ ] **Step 3: Write impact.py**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_impact.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/impact.py tests/test_impact.py
git commit -m "Add blast-radius impact analysis"
```

---

### Task 9: Search module

**Files:**
- Create: `claude_graph/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `GraphStore`.
- Produces: `search_nodes(store: GraphStore, query: str, limit: int = 20) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_search.py`:
```python
from claude_graph.graph_store import GraphStore
from claude_graph.search import search_nodes


def test_search_finds_node_by_name_substring(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file(path)")
    store.add_node("a.py", "function", "unrelated", 3, 4, "unrelated()")
    store.conn.commit()

    results = search_nodes(store, "parse")

    assert any(r["name"] == "parse_file" for r in results)
    assert not any(r["name"] == "unrelated" for r in results)
    store.close()


def test_search_respects_limit(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    for i in range(5):
        store.add_node("a.py", "function", f"helper_{i}", i, i + 1, f"helper_{i}()")
    store.conn.commit()

    results = search_nodes(store, "helper", limit=2)

    assert len(results) <= 2
    store.close()


def test_search_returns_empty_list_for_no_match(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "foo", 1, 2, "foo()")
    store.conn.commit()

    assert search_nodes(store, "nonexistent_xyz") == []
    store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_search.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.search'`

- [ ] **Step 3: Write search.py**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_search.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/search.py tests/test_search.py
git commit -m "Add FTS5 keyword search with LIKE fallback"
```

---

### Task 10: CLI (build, update, status)

**Files:**
- Create: `claude_graph/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_graph`, `update_graph`, `GraphStore`, `find_repo_root`, `NotAGitRepoError`.
- Produces: `main(argv: list[str] | None = None) -> None`; `build_parser() -> argparse.ArgumentParser`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
import json
import subprocess
from pathlib import Path

import pytest

from claude_graph.cli import main


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    return tmp_path


def test_build_command_creates_graph(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    assert (repo / ".claude-graph" / "graph.db").exists()
    output = json.loads(capsys.readouterr().out)
    assert output["nodes"] >= 1


def test_status_command_reports_no_graph(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["status", "--repo", str(repo)])
    assert "No graph found" in capsys.readouterr().out


def test_update_command_after_build(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    (repo / "b.py").write_text("def bar():\n    return 2\n")
    _git("add", "-A", cwd=repo)

    main(["update", "--repo", str(repo)])
    output = json.loads(capsys.readouterr().out)
    assert output["files"] == 2


def test_build_outside_git_repo_exits_with_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["build", "--repo", str(tmp_path)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.cli'`

- [ ] **Step 3: Write cli.py**

```python
"""Command-line interface for claude-graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from claude_graph.build import build_graph, update_graph
from claude_graph.graph_store import GraphStore
from claude_graph.repo import NotAGitRepoError, find_repo_root


def _resolve_repo_root(repo_arg: str | None) -> Path:
    start = Path(repo_arg).resolve() if repo_arg else Path.cwd()
    try:
        return find_repo_root(start)
    except NotAGitRepoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_build(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    stats = build_graph(repo_root, full_rebuild=True)
    print(json.dumps(stats, indent=2))


def _cmd_update(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    stats = update_graph(repo_root)
    print(json.dumps(stats, indent=2))


def _cmd_status(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    db_path = repo_root / ".claude-graph" / "graph.db"
    if not db_path.exists():
        print("No graph found. Run 'claude-graph build' first.")
        return
    with GraphStore(db_path) as store:
        print(json.dumps(store.stats(), indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("build", _cmd_build), ("update", _cmd_update), ("status", _cmd_status)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=None, help="Repository root (default: current directory)")
        sub.set_defaults(func=handler)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add claude_graph/cli.py tests/test_cli.py
git commit -m "Add CLI: build, update, status"
```

---

### Task 11: Install command (.mcp.json + skills) and CLI wiring

**Files:**
- Create: `claude_graph/install.py`
- Modify: `claude_graph/cli.py` (add `install` subcommand)
- Test: `tests/test_install.py`
- Modify: `tests/test_cli.py` (add install-command test)

**Interfaces:**
- Consumes: nothing new.
- Produces: `install_claude_code(repo_root: Path) -> dict` (keys `mcp_config`, `skills`).

- [ ] **Step 1: Write the failing tests**

`tests/test_install.py`:
```python
import json

from claude_graph.install import install_claude_code


def test_install_writes_mcp_config(tmp_path):
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["claude-graph"]["command"] == "claude-graph"
    assert config["mcpServers"]["claude-graph"]["args"] == ["serve", "--repo", str(tmp_path)]


def test_install_writes_skills(tmp_path):
    install_claude_code(tmp_path)
    assert (tmp_path / ".claude" / "skills" / "build-graph" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "review-changes" / "SKILL.md").exists()


def test_install_preserves_existing_mcp_config_entries(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other-server": {"command": "other"}}})
    )
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other-server" in config["mcpServers"]
    assert "claude-graph" in config["mcpServers"]


def test_install_is_idempotent(tmp_path):
    install_claude_code(tmp_path)
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert list(config["mcpServers"].keys()) == ["claude-graph"]
```

Append to `tests/test_cli.py`:
```python
def test_install_command_writes_config_and_skills(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["install", "--repo", str(repo)])
    assert (repo / ".mcp.json").exists()
    assert (repo / ".claude" / "skills" / "build-graph" / "SKILL.md").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_install.py tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.install'`

- [ ] **Step 3: Write install.py and wire it into cli.py**

`claude_graph/install.py`:
```python
"""Writes the Claude-Code-only MCP registration and skill files into a
repo. This is the entire 'install' surface of claude-graph: one
repo-local JSON file and two markdown files. No home-directory writes,
no other editor/platform detection, no hooks."""

from __future__ import annotations

import json
from pathlib import Path

BUILD_GRAPH_SKILL = """---
name: build-graph
description: Build or update the claude-graph knowledge graph for this repository.
---

# Build Graph

1. Call `get_graph_stats` to check whether a graph already exists.
2. If it does not exist, call `build_or_update_graph` to run a full build.
3. If it exists, call `build_or_update_graph` to run an incremental update.
4. Report the resulting node/edge/file counts.
"""

REVIEW_CHANGES_SKILL = """---
name: review-changes
description: Review the current uncommitted changes using blast-radius and test-coverage analysis from the claude-graph knowledge graph.
---

# Review Changes

1. Run `git diff --name-only` to find the changed files.
2. Call `get_impact_radius_tool` with those file paths to find affected
   callers, importers, and tests.
3. Call `query_graph_tool` with pattern `file_summary` on each changed
   file to see what functions/classes it defines.
4. Summarize: what changed, what could break (callers/importers), and
   whether covering tests exist. Flag any changed function with no
   corresponding test file.
"""


def _install_mcp_config(repo_root: Path) -> Path:
    config_path = repo_root / ".mcp.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}
    config.setdefault("mcpServers", {})
    config["mcpServers"]["claude-graph"] = {
        "command": "claude-graph",
        "args": ["serve", "--repo", str(repo_root)],
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _install_skills(repo_root: Path) -> list[Path]:
    skills_dir = repo_root / ".claude" / "skills"
    written = []
    for name, content in (("build-graph", BUILD_GRAPH_SKILL), ("review-changes", REVIEW_CHANGES_SKILL)):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")
        written.append(skill_path)
    return written


def install_claude_code(repo_root: Path) -> dict:
    """Writes .mcp.json and .claude/skills/*/SKILL.md into repo_root.
    Idempotent: running twice produces the same result, no duplicate
    entries or files."""
    config_path = _install_mcp_config(repo_root)
    skill_paths = _install_skills(repo_root)
    return {"mcp_config": str(config_path), "skills": [str(p) for p in skill_paths]}
```

In `claude_graph/cli.py`, add the import and the new command:
```python
from claude_graph.install import install_claude_code
```

```python
def _cmd_install(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    result = install_claude_code(repo_root)
    print(json.dumps(result, indent=2))
```

Change the subcommand loop in `build_parser` to:
```python
    for name, handler in (
        ("build", _cmd_build),
        ("update", _cmd_update),
        ("status", _cmd_status),
        ("install", _cmd_install),
    ):
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_install.py tests/test_cli.py -v`
Expected: 5 passed (4 from test_install.py + 1 new in test_cli.py; existing test_cli.py tests still pass)

- [ ] **Step 5: Commit**

```bash
git add claude_graph/install.py claude_graph/cli.py tests/test_install.py tests/test_cli.py
git commit -m "Add Claude-Code-only install: .mcp.json + skills, no other platforms or hooks"
```

---

### Task 12: MCP server wiring + serve CLI command

**Files:**
- Create: `claude_graph/mcp_server.py`
- Modify: `claude_graph/cli.py` (add `serve` subcommand)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `build_graph`, `update_graph`, `GraphStore`, `query_graph`, `get_impact_radius`, `search_nodes`.
- Produces: `create_server(repo_root: Path) -> FastMCP`; `serve(repo_root: Path) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py`:
```python
import asyncio
import json
import subprocess
from pathlib import Path

from claude_graph.mcp_server import create_server


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    return tmp_path


def _call(app, name, args):
    result = asyncio.run(app.call_tool(name, args))
    return json.loads(result[0].text)


def test_lists_expected_tools(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    tools = asyncio.run(app.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "build_or_update_graph",
        "get_graph_stats",
        "query_graph_tool",
        "get_impact_radius_tool",
        "search_nodes_tool",
    }


def test_build_then_query_via_mcp_tools(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)

    build_result = _call(app, "build_or_update_graph", {})
    assert build_result["nodes"] >= 2

    query_result = _call(app, "query_graph_tool", {"pattern": "callers_of", "target": "bar"})
    first = query_result["result"][0]
    assert first["file"] == "a.py"
    assert first["name"] == "foo"


def test_search_nodes_tool_finds_function(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    _call(app, "build_or_update_graph", {})
    results = _call(app, "search_nodes_tool", {"query": "foo"})
    assert any(r["name"] == "foo" for r in results)


def test_get_impact_radius_tool(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    _call(app, "build_or_update_graph", {})
    impact = _call(app, "get_impact_radius_tool", {"changed_files": ["a.py"]})
    assert impact["changed_files"] == ["a.py"]


def test_get_graph_stats_before_build_returns_zeroes(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    stats = _call(app, "get_graph_stats", {})
    assert stats == {"files": 0, "nodes": 0, "edges": 0, "languages": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_graph.mcp_server'`

- [ ] **Step 3: Write mcp_server.py and wire serve into cli.py**

`claude_graph/mcp_server.py`:
```python
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
    def get_impact_radius_tool(changed_files: list[str]) -> dict:
        """Blast radius of the given changed file paths: callers,
        importers, and tests that could be affected."""
        with GraphStore(_db_path(repo_root)) as store:
            return get_impact_radius(store, changed_files)

    @app.tool()
    def search_nodes_tool(query: str) -> list[dict]:
        """Keyword search over function/class names and signatures."""
        with GraphStore(_db_path(repo_root)) as store:
            return search_nodes(store, query)

    return app


def serve(repo_root: Path) -> None:
    app = create_server(repo_root)
    app.run(transport="stdio")
```

In `claude_graph/cli.py`, add:
```python
def _cmd_serve(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    from claude_graph.mcp_server import serve

    serve(repo_root)
```

Add `("serve", _cmd_serve)` to the subcommand tuple in `build_parser`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: 5 passed

- [ ] **Step 5: Manually verify the stdio server starts**

Run: `claude-graph build --repo /tmp/some-test-repo && claude-graph serve --repo /tmp/some-test-repo` in one terminal, confirm it starts without error and blocks waiting on stdin (Ctrl-C to stop). This step is manual because driving a real stdio MCP handshake needs a live client; the tool wiring itself is covered by `test_mcp_server.py`.

- [ ] **Step 6: Commit**

```bash
git add claude_graph/mcp_server.py claude_graph/cli.py tests/test_mcp_server.py
git commit -m "Add stdio MCP server exposing the 5 claude-graph tools"
```

---

### Task 13: No-network test, README, and packaging finalize

**Files:**
- Create: `tests/test_no_network.py`
- Modify: `README.md` (replace placeholder with full docs)

**Interfaces:**
- Consumes: `build_graph`, `GraphStore`, `query_graph`, `get_impact_radius`, `search_nodes`.
- Produces: nothing new (this task is verification + documentation).

- [ ] **Step 1: Write the no-network test**

`tests/test_no_network.py`:
```python
"""Proof that claude-graph never makes a network call during normal
operation: build, query, impact, and search all succeed with outbound
sockets disabled."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.impact import get_impact_radius
from claude_graph.query import query_graph
from claude_graph.search import search_nodes


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(self, *args, **kwargs):
        raise AssertionError("network connection attempted during a claude-graph operation")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


def test_full_workflow_makes_no_network_calls(tmp_path, no_network):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    (tmp_path / "test_a.py").write_text("def test_foo():\n    assert foo() == 1\n")
    _git("add", "-A", cwd=tmp_path)

    stats = build_graph(tmp_path, full_rebuild=True)
    assert stats["nodes"] > 0

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        assert query_graph(store, "callers_of", "bar") != []
        assert search_nodes(store, "foo") != []
        impact = get_impact_radius(store, ["a.py"])
        assert impact["changed_files"] == ["a.py"]
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `pytest tests/test_no_network.py -v`
Expected: 1 passed. (If it fails with the `AssertionError` from `_blocked`, something in the build/query/impact/search path is opening a socket — investigate before proceeding; this must pass before the tool can be called local-only.)

- [ ] **Step 3: Write the full README.md**

Replace `README.md` entirely with:
```markdown
# claude-graph

A local knowledge graph of your codebase, built specifically for
**Claude Code on macOS**. Parses your repo with Tree-sitter, stores a
structural graph (functions, classes, calls, imports, test coverage) in
a local SQLite file, and exposes it to Claude Code over MCP so it can
answer "what calls this", "what would break if I change this file", and
"is this covered by a test" without reading the whole repo.

## Why this exists, and what it deliberately doesn't do

Built for a corporate setting with one hard requirement: **everything
happens locally, with zero network calls, ever.**

- No cloud or local embeddings/semantic search. Claude Code itself is
  already the LLM in the loop — it reads the candidates this tool
  returns (keyword search + graph neighbors) and does the semantic
  reasoning itself. No vectors, no model downloads, no API calls.
- No hooks. Nothing runs automatically when you edit a file. You (or
  Claude Code) call `build_or_update_graph` explicitly.
- No multi-platform support. This only configures Claude Code. It won't
  touch Cursor, Windsurf, Zed, or anything else.
- No home-directory writes. Everything this tool writes lives inside
  the repository you run it in (`.claude-graph/`, `.mcp.json`,
  `.claude/skills/`).
- No telemetry, no daemon, no multi-repo registry.

See `tests/test_no_network.py` for the automated proof: it runs a full
build + query + impact + search cycle with outbound sockets disabled
and asserts nothing tries to connect anywhere.

## Requirements

- macOS
- Python 3.11+
- git

## Install

```bash
git clone <this-repo-url>
cd claude-graph
pip install -e .
```

Then, inside the project you want a graph for:

```bash
cd /path/to/your/project
claude-graph install   # writes .mcp.json and .claude/skills/ in that repo
claude-graph build      # parses the repo and writes .claude-graph/graph.db
```

Restart Claude Code (or run `/mcp` to confirm `claude-graph` is
connected) and ask it something structural, e.g. "what calls
`parse_file` in this repo?"

## CLI

| Command | What it does |
|---|---|
| `claude-graph build` | Full parse of every git-tracked file |
| `claude-graph update` | Re-parses only changed files since the last build |
| `claude-graph status` | Prints node/edge/file counts |
| `claude-graph install` | Writes `.mcp.json` and `.claude/skills/` for this repo |
| `claude-graph serve` | Starts the MCP server (stdio) — Claude Code launches this itself |

## MCP tools

| Tool | Purpose |
|---|---|
| `build_or_update_graph` | Full build if no graph exists, incremental update otherwise |
| `get_graph_stats` | Node/edge/file counts, languages detected |
| `query_graph_tool` | `callers_of` / `callees_of` / `imports_of` / `tests_for` / `file_summary` |
| `get_impact_radius_tool` | Blast radius of a set of changed files |
| `search_nodes_tool` | Keyword search over function/class names and signatures |

## Supported languages

Python, JavaScript, TypeScript, TSX out of the box. Add more by
dropping a `.claude-graph/languages.toml` into your repo — see
`claude_graph/default_languages.toml` for the schema (extensions,
tree-sitter grammar name, and the node types that count as a
function/class/import/call for that grammar). No code change needed.

## Known limitations

- Call-graph edges are resolved by AST structure and name matching, not
  a real compiler — cross-file calls resolve by function name globally,
  so two files with a same-named function can produce over-broad
  `callers_of` results. This is a deliberate precision/recall
  trade-off: better to flag too much than miss a real caller.
- `tests_for` linking is naming-convention only (`test_foo.py` /
  `foo_test.py` / `foo.spec.ts` / `foo.test.ts` matched against
  `foo.py` / `foo.ts`). Tests that don't follow one of these
  conventions aren't linked.
- Import resolution is best-effort path matching, not real module
  resolution — it won't follow `tsconfig.json` path aliases or Python
  namespace packages.

## For teammates installing this themselves

```bash
git clone <this-repo-url>
cd claude-graph
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all tests across every task pass (repo, graph_store, languages, parser, build, query, impact, search, cli, install, mcp_server, no_network).

- [ ] **Step 5: Verify a clean install from scratch**

```bash
cd /tmp
python3 -m venv clean-test-venv
source clean-test-venv/bin/activate
pip install -e /Users/mohansagar/Documents/projects/claude/claude-graph
claude-graph --help
deactivate
```
Expected: `claude-graph --help` prints usage with `build`, `update`, `status`, `install`, `serve` subcommands, with no errors.

- [ ] **Step 6: Commit**

```bash
git add tests/test_no_network.py README.md
git commit -m "Add no-network proof test and full README"
```

---

## Self-Review Notes

- **Spec coverage:** every architecture layer (parser, store, MCP server), every listed MCP tool (5/5), every CLI command (build/update/status/install/serve), `.claude-graphignore` (Task 1), `.claude-graph/languages.toml` override (Task 3), transaction-wrapped writes (Task 2/6), skip-bad-file error handling (Task 6), and the no-network proof (Task 13) each have a task and a test.
- **Placeholder scan:** no TBD/TODO markers; the one thing that looked like a placeholder (Task 12 Step 5, "manually verify serve starts") is explicitly a manual step because a stdio MCP handshake needs a live client — the tool logic itself is fully covered by `test_mcp_server.py`.
- **Type consistency:** `ParsedNode`/`ParsedCall`/`ParsedImport` field names are identical between Task 4 (definition) and Task 6 (consumption in `build.py`). `GraphStore` method names (`sync_file_nodes`, `clear_outgoing_edges`, `find_module_node`, etc.) are identical between Task 2 (definition) and Tasks 6–9, 12 (consumption). MCP tool names in `mcp_server.py` (Task 12) match the README's table (Task 13) and the `review-changes` skill's tool references (Task 11).
