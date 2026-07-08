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


# TDD tests for query sanitization and wildcard escaping
def test_fts_search_handles_hyphen_without_crashing(tmp_path):
    """FTS path robustness: hyphens should not crash."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.conn.commit()

    results = search_nodes(store, "foo-bar")
    assert results == []
    store.close()


def test_fts_search_handles_quote_without_crashing(tmp_path):
    """FTS path robustness: single quotes should not crash."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.conn.commit()

    results = search_nodes(store, "O'Brien")
    assert results == []
    store.close()


def test_fts_search_handles_empty_query(tmp_path):
    """FTS path robustness: empty query should return empty list."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.conn.commit()

    results = search_nodes(store, "")
    assert results == []
    store.close()


def test_fts_search_handles_whitespace_only_query(tmp_path):
    """FTS path robustness: whitespace-only query should return empty list."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.conn.commit()

    results = search_nodes(store, "   ")
    assert results == []
    store.close()


def test_fts_search_handles_hyphen_in_search_term(tmp_path):
    """FTS path: hyphenated search term should still work."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse-file", 1, 2, "parse-file()")
    store.conn.commit()

    # Should not crash; may return empty or may return the node (both acceptable)
    results = search_nodes(store, "parse-file")
    # Either result is fine - the point is it shouldn't crash
    store.close()


def test_fts_search_still_finds_normal_query(tmp_path):
    """FTS path: normal queries should still work."""
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.conn.commit()

    results = search_nodes(store, "parse_file")
    assert any(r["name"] == "parse_file" for r in results)
    store.close()


def test_like_search_literal_underscore(tmp_path):
    """LIKE path: underscore should be literal, not a wildcard."""
    store = GraphStore(tmp_path / "graph.db")
    store.fts_enabled = False  # Force LIKE path
    store.add_node("a.py", "function", "a_b", 1, 2, "a_b()")
    store.add_node("a.py", "function", "axb", 1, 2, "axb()")
    store.conn.commit()

    results = search_nodes(store, "a_b")
    names = [r["name"] for r in results]
    assert "a_b" in names
    assert "axb" not in names
    store.close()


def test_like_search_literal_percent(tmp_path):
    """LIKE path: percent should be literal, not a wildcard."""
    store = GraphStore(tmp_path / "graph.db")
    store.fts_enabled = False  # Force LIKE path
    store.add_node("a.py", "function", "a%b", 1, 2, "a%b()")
    store.add_node("a.py", "function", "axb", 1, 2, "axb()")
    store.conn.commit()

    results = search_nodes(store, "a%b")
    names = [r["name"] for r in results]
    assert "a%b" in names
    assert "axb" not in names
    store.close()


def test_like_search_still_matches_substring(tmp_path):
    """LIKE path: normal substring matching should still work."""
    store = GraphStore(tmp_path / "graph.db")
    store.fts_enabled = False  # Force LIKE path
    store.add_node("a.py", "function", "parse_file", 1, 2, "parse_file()")
    store.add_node("a.py", "function", "unrelated", 3, 4, "unrelated()")
    store.conn.commit()

    results = search_nodes(store, "parse")
    names = [r["name"] for r in results]
    assert "parse_file" in names
    assert "unrelated" not in names
    store.close()


def test_like_search_results_ordered(tmp_path):
    """LIKE path: results should be ordered deterministically by name."""
    store = GraphStore(tmp_path / "graph.db")
    store.fts_enabled = False  # Force LIKE path
    store.add_node("a.py", "function", "zebra", 1, 2, "zebra()")
    store.add_node("a.py", "function", "apple", 1, 2, "apple()")
    store.add_node("a.py", "function", "banana", 1, 2, "banana()")
    store.conn.commit()

    results = search_nodes(store, "")
    names = [r["name"] for r in results]
    # With ORDER BY name, should be in alphabetical order
    assert names == sorted(names)
    store.close()
