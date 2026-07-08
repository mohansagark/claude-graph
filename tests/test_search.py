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
