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
    assert ids1[("function", "foo")] == ids2[("function", "foo")]
    row = store.get_node(ids2[("function", "foo")])
    assert row["end_line"] == 3
    assert row["signature"] == "def foo(x):"
    store.close()


def test_sync_file_nodes_removes_stale_nodes_and_their_edges(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    ids1 = store.sync_file_nodes(
        "a.py", [("function", "foo", 1, 2, ""), ("function", "bar", 3, 4, "")]
    )
    other = store.add_node("b.py", "function", "caller", 1, 2, "")
    store.add_edge(other, ids1[("function", "bar")], "calls")
    store.conn.commit()

    ids2 = store.sync_file_nodes("a.py", [("function", "foo", 1, 2, "")])  # bar removed
    store.conn.commit()

    assert ("function", "bar") not in ids2
    assert store.edges_by_dst(ids1[("function", "bar")], "calls") == []
    store.close()


def test_sync_file_nodes_keys_by_kind_and_name_not_name_alone(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    ids = store.sync_file_nodes(
        "a.py", [("function", "Foo", 1, 2, ""), ("class", "Foo", 3, 5, "")]
    )
    store.conn.commit()
    assert ("function", "Foo") in ids
    assert ("class", "Foo") in ids
    assert ids[("function", "Foo")] != ids[("class", "Foo")]
    assert store.get_node(ids[("function", "Foo")])["kind"] == "function"
    assert store.get_node(ids[("class", "Foo")])["kind"] == "class"
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


def test_all_nodes_returns_every_node(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.add_node("a.py", "function", "foo", 1, 2, "")
    store.add_node("b.py", "class", "Bar", 1, 4, "")
    store.conn.commit()

    names = {row["name"] for row in store.all_nodes()}
    assert names == {"foo", "Bar"}
    store.close()


def test_all_edges_returns_every_edge(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    a = store.add_node("a.py", "function", "foo", 1, 2, "")
    b = store.add_node("b.py", "function", "bar", 1, 2, "")
    store.add_edge(a, b, "calls")
    store.conn.commit()

    edges = store.all_edges()
    assert len(edges) == 1
    assert edges[0]["src"] == a
    assert edges[0]["dst"] == b
    assert edges[0]["kind"] == "calls"
    store.close()
