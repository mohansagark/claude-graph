import subprocess
from pathlib import Path

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.query import callees_of, callers_of, file_summary, imports_of, query_graph
from claude_graph.query import tests_for as get_tests_for


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
        results = get_tests_for(store, "b.py")
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
