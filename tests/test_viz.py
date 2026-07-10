import json
import subprocess
from pathlib import Path

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.viz import render_graph


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    return tmp_path


def _embedded_payload(html: str) -> dict:
    start = html.index("const DATA = ") + len("const DATA = ")
    end = html.index(";\n", start)
    return json.loads(html[start:end])


def test_render_graph_full_scope_writes_html(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        result = render_graph(store, output, scope="full")

    assert result["path"] == str(output)
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert result["node_count"] > 0
    assert result["edge_count"] > 0


def test_render_graph_full_scope_payload_matches_counts(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        result = render_graph(store, output, scope="full")

    payload = _embedded_payload(output.read_text(encoding="utf-8"))
    assert len(payload["nodes"]) == result["node_count"]
    assert len(payload["edges"]) == result["edge_count"]
    assert any(n["name"] == "foo" for n in payload["nodes"])
    assert payload["highlight_ids"] == []


def test_render_graph_output_has_no_network_call_vectors(tmp_path):
    """The vendored D3 library's own minified source legitimately contains
    http:// substrings (XML namespace URIs like http://www.w3.org/2000/svg,
    used by DOM APIs, never fetched over the network) and the token
    "fetch(" (its unused d3-fetch module), so a blanket ban across the
    whole file always false-positives on the vendored blob. That blob is
    pinned and committed to git (human-reviewable), and test_no_network.py
    separately proves render_graph makes no socket connections at all. What
    this test guards is everything render_graph itself generates around
    that blob (the template + embedded JSON payload): it must contain zero
    network-call vectors."""
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        render_graph(store, output, scope="full")

    html = output.read_text(encoding="utf-8")
    d3_script = (Path(__file__).parent.parent / "claude_graph" / "static" / "d3.v7.min.js").read_text(
        encoding="utf-8"
    )
    generated = html.replace(d3_script, "")

    for vector in ("http://", "https://", "fetch(", "XMLHttpRequest", "WebSocket", "<script src=", "<link"):
        assert vector not in generated, f"network-call vector {vector!r} found outside vendored D3 blob"


def test_render_graph_invalid_scope_raises(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        try:
            render_graph(store, output, scope="bogus")
            assert False, "expected ValueError"
        except ValueError:
            pass
