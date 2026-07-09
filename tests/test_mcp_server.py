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
    # Tools whose return type annotation is a bare `list[...]` (e.g.
    # search_nodes_tool) trigger this mcp SDK version's structured-output
    # path: call_tool() returns a (content_blocks, structured_dict) tuple
    # instead of a plain content-block sequence, and the structured dict
    # wraps the list as {"result": [...]}. Tools annotated `-> dict` return
    # a plain content-block list whose first block's text is the JSON dict.
    if isinstance(result, tuple):
        _, structured = result
        return structured.get("result", structured)
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


def test_get_impact_radius_tool_exposes_depth_in_schema(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    tools = asyncio.run(app.list_tools())
    impact_tool = next(t for t in tools if t.name == "get_impact_radius_tool")
    assert "depth" in impact_tool.inputSchema["properties"]


def test_get_impact_radius_tool_depth_limits_callers(tmp_path):
    # c() <- b() <- a(): a two-hop caller chain into c.py.
    repo = tmp_path
    _git("init", "-q", cwd=repo)
    (repo / "c.py").write_text("def c():\n    return 1\n")
    (repo / "b.py").write_text("from c import c\n\ndef b():\n    return c()\n")
    (repo / "a.py").write_text("from b import b\n\ndef a():\n    return b()\n")
    _git("add", "-A", cwd=repo)

    app = create_server(repo)
    _call(app, "build_or_update_graph", {})

    shallow = _call(app, "get_impact_radius_tool", {"changed_files": ["c.py"], "depth": 1})
    shallow_names = {c["name"] for c in shallow["callers"]}
    assert shallow_names == {"b"}

    deep = _call(app, "get_impact_radius_tool", {"changed_files": ["c.py"], "depth": 2})
    deep_names = {c["name"] for c in deep["callers"]}
    assert deep_names == {"a", "b"}


def test_get_graph_stats_before_build_returns_zeroes(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    stats = _call(app, "get_graph_stats", {})
    assert stats == {"files": 0, "nodes": 0, "edges": 0, "languages": []}
