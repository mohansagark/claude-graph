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


def test_impact_radius_traverses_through_changed_set_nodes(tmp_path):
    """Regression test: seed-set nodes must be traversed through when discovered
    as callers, ensuring the blast radius is complete when using multi-file changed_files.

    Concrete failure case from issue:
    - changed_files = ["b.py", "c.py"]
    - c.py defines mid() which calls helper() in b.py
    - a.py defines top() which calls mid()
    - seed_ids = {all nodes in b.py and c.py} = {helper, mid}

    Bug: when mid is discovered as a caller of helper, it is skipped entirely
    because mid is in seed_ids, preventing traversal to find top.

    With the fix: mid should still be traversed (added to frontier) even though
    it's in seed_ids, allowing discovery of top. mid is excluded from OUTPUT
    (because it's on a changed file) but must be traversed through.
    """
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "from c import mid\n\ndef top():\n    return mid()\n")
    _write(tmp_path, "b.py", "def helper():\n    return 1\n")
    _write(tmp_path, "c.py", "from b import helper\n\ndef mid():\n    return helper()\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        impact = get_impact_radius(store, ["b.py", "c.py"], depth=2)

    # Collect results as a dict mapping name to depth
    names_by_depth = {c["name"]: c["depth"] for c in impact["callers"]}

    # The key assertion: top MUST be found and in the results
    # Without the fix, mid is skipped during traversal when discovered as a caller
    # of helper (because mid is in seed_ids), which can prevent discovery of top
    # (or find it at incorrect depth). With the fix, traversal goes through mid.
    assert "top" in names_by_depth, \
        f"top should be found; callers found: {names_by_depth}"

    # mid should NOT be in results (it's on changed file c.py, so in seed_ids)
    assert "mid" not in names_by_depth, \
        "mid should not be in results (it's on a changed file)"
