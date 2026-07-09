"""Proof that claude-graph never makes a network call during normal
operation: build, query, impact, search, and MCP server startup all
succeed with outbound sockets disabled.

Note: subprocess children spawned by claude-graph (e.g., git ls-files)
are outside the monkeypatch's reach and may make network calls, but
claude-graph's core Python code never does."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from pathlib import Path

import pytest

from claude_graph.build import build_graph
from claude_graph.graph_store import GraphStore
from claude_graph.impact import get_impact_radius
from claude_graph.mcp_server import create_server
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

    # Test MCP server startup (construction registers all 5 tools)
    app = create_server(tmp_path)
    tools = asyncio.run(app.list_tools())
    assert len(tools) == 5
