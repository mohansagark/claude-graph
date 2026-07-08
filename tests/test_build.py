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


def test_unsupported_files_get_no_ghost_module_node(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def foo():\n    return 1\n")
    _write(tmp_path, "README.md", "# hello\n")
    _write(tmp_path, "data.json", "{}\n")
    _git("add", "-A", cwd=tmp_path)

    stats = build_graph(tmp_path, full_rebuild=True)
    assert stats["nodes"] == 2  # module a.py + foo

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        assert store.find_module_node("README.md") is None
        assert store.find_module_node("data.json") is None

    (tmp_path / "a.py").unlink()
    _git("add", "-A", cwd=tmp_path)
    stats = update_graph(tmp_path)
    assert stats["files"] == 0


def test_call_caller_resolves_to_function_not_same_named_class(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(
        tmp_path,
        "a.py",
        "def Foo():\n    return helper()\n\n\ndef helper():\n    return 1\n\n\nclass Foo:\n    pass\n",
    )
    _git("add", "-A", cwd=tmp_path)

    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        helper = store.find_nodes_by_name("helper", kind="function")[0]
        callers = store.edges_by_dst(helper["id"], "calls")
        assert len(callers) == 1
        caller_node = store.get_node(callers[0]["src"])
        assert caller_node["kind"] == "function"
        assert caller_node["name"] == "Foo"


def test_update_removes_stale_call_edge_when_caller_no_longer_calls(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _write(tmp_path, "a.py", "def helper():\n    return 1\n")
    _write(tmp_path, "b.py", "from a import helper\n\ndef foo():\n    return helper()\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        helper = store.find_nodes_by_name("helper", kind="function")[0]
        assert len(store.edges_by_dst(helper["id"], "calls")) == 1

    _write(tmp_path, "b.py", "def foo():\n    return 2\n")
    _git("add", "-A", cwd=tmp_path)
    update_graph(tmp_path)

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        helper = store.find_nodes_by_name("helper", kind="function")[0]
        assert store.edges_by_dst(helper["id"], "calls") == []
