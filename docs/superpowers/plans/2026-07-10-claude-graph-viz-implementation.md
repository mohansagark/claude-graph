# claude-graph interactive graph visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained, offline, interactive HTML graph view of the codebase claude-graph already indexes — exposed both as `claude-graph viz` (CLI) and `render_graph_tool` (MCP), scoped to the whole graph, one symbol's neighborhood, or a changed-files impact radius.

**Architecture:** A new `claude_graph/viz.py` module reads from the existing `GraphStore`, assembles a JSON node/edge payload, and substitutes it into a static HTML template that has a vendored copy of D3 v7 inlined into it. The output is one `.html` file opened directly via `file://` — no server, no CDN script tag, no network call at any point.

**Tech Stack:** Python 3.11+ (existing stack), vendored `d3.v7.min.js` (ISC license) for the force-directed layout/zoom/drag, vanilla JS for the rest of the interaction — no new Python dependencies.

## Global Constraints

- Zero network calls, ever — the generated HTML must contain no `http://` or `https://` substring anywhere (proves D3 is truly inlined, not CDN-loaded). Source: `docs/superpowers/specs/2026-07-10-claude-graph-viz-design.md` Rendering section.
- Neither the CLI `viz` command nor the `render_graph_tool` MCP tool ever opens a browser automatically — both only write the file and report its path. Source: spec Non-goals.
- `render_graph_tool`'s output path is fixed at `.claude-graph/graph.html` (not caller-choosable over MCP). The CLI's `-o/--output` flag may override it. Source: spec CLI/MCP sections.
- `scope` is one of `"full"`, `"symbol"`, `"impact"`; `"symbol"` requires `symbol`, `"impact"` requires `changed_files`; any other combination raises `ValueError`. Source: spec Architecture section.
- `--symbol` and `--impact` are mutually exclusive CLI flags. Source: spec CLI section.
- No node cap / pagination in this plan — out of scope, documented as a known limitation. Source: spec Known limitations.

---

### Task 1: `GraphStore.all_nodes()` / `all_edges()`

**Files:**
- Modify: `claude_graph/graph_store.py` (add two methods after `nodes_for_file`, i.e. after line 226)
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Produces: `GraphStore.all_nodes() -> list[sqlite3.Row]` (every row in `nodes`, ordered by `id`), `GraphStore.all_edges() -> list[sqlite3.Row]` (every row in `edges`, ordered by `id`). Later tasks (Task 2) call these directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_graph_store.py -k "all_nodes or all_edges" -v`
Expected: FAIL with `AttributeError: 'GraphStore' object has no attribute 'all_nodes'`

- [ ] **Step 3: Implement the methods**

In `claude_graph/graph_store.py`, add immediately after the `nodes_for_file` method (which ends around line 229, right before the `# -- edges --` comment):

```python
    def all_nodes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
```

And after the `edges_by_src` method (right before the `# -- stats --` comment):

```python
    def all_edges(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM edges ORDER BY id").fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_graph_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add claude_graph/graph_store.py tests/test_graph_store.py
git commit -m "Add GraphStore.all_nodes()/all_edges() for full-graph reads"
```

---

### Task 2: Vendor D3, static template, `render_graph` for `scope="full"`

**Files:**
- Create: `claude_graph/static/d3.v7.min.js` (vendored, not hand-written)
- Create: `claude_graph/static/d3-LICENSE` (vendored, not hand-written)
- Create: `claude_graph/static/graph_template.html`
- Create: `claude_graph/viz.py`
- Modify: `pyproject.toml` (force-include the new static dir)
- Test: `tests/test_viz.py`

**Interfaces:**
- Consumes: `GraphStore.all_nodes()`, `GraphStore.all_edges()` (Task 1).
- Produces: `render_graph(store: GraphStore, output_path: Path, scope: str = "full", symbol: str | None = None, changed_files: list[str] | None = None, depth: int = 2) -> dict` returning `{"path": str, "node_count": int, "edge_count": int}`. Tasks 3-5 call this with other `scope` values.

- [ ] **Step 1: Vendor D3 v7 and its license**

```bash
mkdir -p claude_graph/static
curl -sS -o claude_graph/static/d3.v7.min.js https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js
curl -sS -o claude_graph/static/d3-LICENSE https://raw.githubusercontent.com/d3/d3/main/LICENSE
```

Verify both downloaded and are non-empty:

```bash
wc -c claude_graph/static/d3.v7.min.js claude_graph/static/d3-LICENSE
```

Expected: `d3.v7.min.js` is on the order of 250-290KB; `d3-LICENSE` is a few hundred bytes and contains the string `Bostock`.

- [ ] **Step 2: Create the HTML template**

Create `claude_graph/static/graph_template.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>claude-graph</title>
<style>
  :root { color-scheme: light dark; }
  html, body { margin: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1117; color: #e6e6e6; }
  svg { width: 100%; height: 100%; display: block; }
  .node { cursor: pointer; }
  .node circle { stroke: #0f1117; stroke-width: 1.5px; }
  .node text { font-size: 10px; fill: #e6e6e6; pointer-events: none; }
  .link { stroke-opacity: 0.6; fill: none; }
  .link.calls { stroke: #6ea8fe; }
  .link.imports { stroke: #f2b880; stroke-dasharray: 4 3; }
  .link.tests_for { stroke: #7ee787; stroke-dasharray: 1 3; }
  .dimmed { opacity: 0.08; }
  #panel { position: fixed; top: 12px; right: 12px; width: 260px; background: #1a1d27; border: 1px solid #30354a; border-radius: 8px; padding: 12px; font-size: 12px; display: none; }
  #panel h3 { margin: 0 0 6px; font-size: 13px; word-break: break-all; }
  #panel div { margin: 2px 0; color: #b6bac9; }
  #legend { position: fixed; bottom: 12px; left: 12px; background: #1a1d27; border: 1px solid #30354a; border-radius: 8px; padding: 10px 12px; font-size: 11px; }
  #legend div { display: flex; align-items: center; gap: 6px; margin: 3px 0; }
  #legend .swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  #search { position: fixed; top: 12px; left: 12px; width: 220px; padding: 6px 8px; border-radius: 6px; border: 1px solid #30354a; background: #1a1d27; color: #e6e6e6; font-size: 12px; }
  #empty { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; color: #7c8093; font-size: 14px; }
  #count { position: fixed; bottom: 12px; right: 12px; font-size: 11px; color: #7c8093; }
</style>
</head>
<body>
<input id="search" type="text" placeholder="Search nodes by name...">
<div id="legend"></div>
<div id="panel"></div>
<div id="count"></div>
<svg></svg>
<script>{{D3_SCRIPT}}</script>
<script>
const DATA = {{DATA_JSON}};

const NODE_COLOR = { function: "#6ea8fe", class: "#f2b880", module: "#7ee787" };
const EDGE_CLASS = { calls: "calls", imports: "imports", tests_for: "tests_for" };

if (DATA.nodes.length === 0) {
  document.body.insertAdjacentHTML("beforeend",
    '<div id="empty">No graph data. Run claude-graph build first.</div>');
} else {
  const svg = d3.select("svg");
  const width = window.innerWidth, height = window.innerHeight;
  const g = svg.append("g");

  svg.call(d3.zoom().scaleExtent([0.1, 6]).on("zoom", (event) => {
    g.attr("transform", event.transform);
  }));

  const nodesById = new Map(DATA.nodes.map(n => [n.id, n]));

  const simulation = d3.forceSimulation(DATA.nodes)
    .force("link", d3.forceLink(DATA.edges).id(d => d.id).distance(70).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-160))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(18));

  const link = g.append("g")
    .selectAll("line")
    .data(DATA.edges)
    .join("line")
    .attr("class", d => "link " + (EDGE_CLASS[d.kind] || ""));

  const node = g.append("g")
    .selectAll("g")
    .data(DATA.nodes)
    .join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", dragstarted)
      .on("drag", dragged)
      .on("end", dragended));

  node.append("circle")
    .attr("r", 7)
    .attr("fill", d => NODE_COLOR[d.kind] || "#999");

  node.append("text")
    .attr("x", 10)
    .attr("y", 4)
    .text(d => d.name);

  node.on("click", (event, d) => {
    event.stopPropagation();
    highlight(d.id);
  });

  svg.on("click", () => clearHighlight());

  function edgeEndpointIds(e) {
    return [typeof e.source === "object" ? e.source.id : e.source,
            typeof e.target === "object" ? e.target.id : e.target];
  }

  function neighborsOf(id) {
    const ids = new Set([id]);
    DATA.edges.forEach(e => {
      const [s, t] = edgeEndpointIds(e);
      if (s === id) ids.add(t);
      if (t === id) ids.add(s);
    });
    return ids;
  }

  function highlight(id) {
    const keep = neighborsOf(id);
    node.classed("dimmed", d => !keep.has(d.id));
    link.classed("dimmed", d => {
      const [s, t] = edgeEndpointIds(d);
      return !(keep.has(s) && keep.has(t));
    });
    showPanel(nodesById.get(id), keep.size - 1);
  }

  function clearHighlight() {
    node.classed("dimmed", false);
    link.classed("dimmed", false);
    document.getElementById("panel").style.display = "none";
  }

  function showPanel(d, connectionCount) {
    const panel = document.getElementById("panel");
    panel.innerHTML = `<h3>${d.name}</h3>
      <div>kind: ${d.kind}</div>
      <div>file: ${d.file}</div>
      <div>line: ${d.line}</div>
      <div>connections: ${connectionCount}</div>`;
    panel.style.display = "block";
  }

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(event, d) { d.fx = event.x; d.fy = event.y; }
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  }

  simulation.on("tick", () => {
    link
      .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });

  document.getElementById("legend").innerHTML = Object.entries(NODE_COLOR).map(([kind, color]) =>
    `<div><span class="swatch" style="background:${color}"></span>${kind}</div>`
  ).join("");

  document.getElementById("count").textContent = `${DATA.nodes.length} nodes, ${DATA.edges.length} edges`;

  document.getElementById("search").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) { node.classed("dimmed", false); link.classed("dimmed", false); return; }
    const matchIds = new Set(DATA.nodes.filter(n => n.name.toLowerCase().includes(q)).map(n => n.id));
    node.classed("dimmed", d => !matchIds.has(d.id));
    link.classed("dimmed", d => {
      const [s, t] = edgeEndpointIds(d);
      return !(matchIds.has(s) || matchIds.has(t));
    });
  });

  if (DATA.highlight_ids && DATA.highlight_ids.length) {
    const ids = new Set(DATA.highlight_ids);
    node.classed("dimmed", d => !ids.has(d.id));
    link.classed("dimmed", d => {
      const [s, t] = edgeEndpointIds(d);
      return !(ids.has(s) && ids.has(t));
    });
    if (DATA.highlight_ids.length === 1) {
      simulation.on("end", () => highlight(DATA.highlight_ids[0]));
    }
  }
}
</script>
</body>
</html>
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_viz.py`:

```python
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


def test_render_graph_output_has_no_network_urls(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        render_graph(store, output, scope="full")

    html = output.read_text(encoding="utf-8")
    assert "http://" not in html
    assert "https://" not in html


def test_render_graph_invalid_scope_raises(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        try:
            render_graph(store, output, scope="bogus")
            assert False, "expected ValueError"
        except ValueError:
            pass
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_viz.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_graph.viz'`

- [ ] **Step 5: Implement `claude_graph/viz.py`**

```python
"""Static, self-contained HTML graph visualization: renders the graph (or
a scoped neighborhood of it) to a single local HTML file with a vendored
D3 force-directed layout. No server, no network — the output is opened
directly via file://."""

from __future__ import annotations

import json
from pathlib import Path

from claude_graph.graph_store import GraphStore

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_PATH = _STATIC_DIR / "graph_template.html"
_D3_PATH = _STATIC_DIR / "d3.v7.min.js"

_VALID_SCOPES = {"full", "symbol", "impact"}


def render_graph(
    store: GraphStore,
    output_path: Path,
    scope: str = "full",
    symbol: str | None = None,
    changed_files: list[str] | None = None,
    depth: int = 2,
) -> dict:
    if scope not in _VALID_SCOPES:
        raise ValueError(f"unknown scope: {scope!r}, must be one of {sorted(_VALID_SCOPES)}")
    if scope == "symbol" and not symbol:
        raise ValueError("scope='symbol' requires a symbol name")
    if scope == "impact" and not changed_files:
        raise ValueError("scope='impact' requires changed_files")

    if scope == "full":
        nodes, edges, highlight_ids = _full_graph(store)
    elif scope == "symbol":
        nodes, edges, highlight_ids = _symbol_neighborhood(store, symbol)
    else:
        nodes, edges, highlight_ids = _impact_neighborhood(store, changed_files, depth)

    payload = {"nodes": nodes, "edges": edges, "highlight_ids": highlight_ids}
    html = _render_html(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    return {"path": str(output_path), "node_count": len(nodes), "edge_count": len(edges)}


def _node_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "file": row["file"], "line": row["start_line"]}


def _full_graph(store: GraphStore) -> tuple[list[dict], list[dict], list[int]]:
    nodes = [_node_dict(row) for row in store.all_nodes()]
    edges = [{"source": row["src"], "target": row["dst"], "kind": row["kind"]} for row in store.all_edges()]
    return nodes, edges, []


def _symbol_neighborhood(store: GraphStore, symbol: str) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3


def _impact_neighborhood(
    store: GraphStore, changed_files: list[str], depth: int
) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3


def _render_html(payload: dict) -> str:
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    d3_script = _D3_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(payload).replace("</", "<\\/")
    html = template.replace("{{D3_SCRIPT}}", d3_script)
    html = html.replace("{{DATA_JSON}}", data_json)
    return html
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_viz.py -v`
Expected: all PASS (the `symbol`/`impact` tests belong to Task 3, not written yet)

- [ ] **Step 7: Force-include the static assets in the wheel build**

In `pyproject.toml`, change:

```toml
[tool.hatch.build.targets.wheel.force-include]
"claude_graph/default_languages.toml" = "claude_graph/default_languages.toml"
```

to:

```toml
[tool.hatch.build.targets.wheel.force-include]
"claude_graph/default_languages.toml" = "claude_graph/default_languages.toml"
"claude_graph/static/d3.v7.min.js" = "claude_graph/static/d3.v7.min.js"
"claude_graph/static/d3-LICENSE" = "claude_graph/static/d3-LICENSE"
"claude_graph/static/graph_template.html" = "claude_graph/static/graph_template.html"
```

- [ ] **Step 8: Commit**

```bash
git add claude_graph/static claude_graph/viz.py pyproject.toml tests/test_viz.py
git commit -m "Add render_graph (scope=full) with vendored D3 self-contained HTML output"
```

---

### Task 3: `render_graph` for `scope="symbol"` and `scope="impact"`

**Files:**
- Modify: `claude_graph/viz.py` (replace the two `NotImplementedError` stubs from Task 2)
- Test: `tests/test_viz.py`

**Interfaces:**
- Consumes: `GraphStore.find_nodes_by_name`, `GraphStore.find_module_node`, `GraphStore.nodes_for_file`, `GraphStore.edges_by_src`, `GraphStore.edges_by_dst`, `GraphStore.get_node` (all pre-existing).
- Produces: fully working `render_graph(..., scope="symbol", symbol=...)` and `render_graph(..., scope="impact", changed_files=..., depth=...)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_viz.py`:

```python
def _embedded_payload_from_path(output: Path) -> dict:
    return _embedded_payload(output.read_text(encoding="utf-8"))


def test_render_graph_symbol_scope_includes_caller_and_callee(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text(
        "def top():\n    return mid()\n\ndef mid():\n    return bottom()\n\ndef bottom():\n    return 1\n"
    )
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    output = tmp_path / ".claude-graph" / "graph.html"

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        result = render_graph(store, output, scope="symbol", symbol="mid")

    payload = _embedded_payload_from_path(output)
    names = {n["name"] for n in payload["nodes"]}
    assert "mid" in names
    assert "top" in names  # caller
    assert "bottom" in names  # callee
    assert result["node_count"] == len(payload["nodes"])


def test_render_graph_symbol_scope_highlights_target(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    output = tmp_path / ".claude-graph" / "graph.html"

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        render_graph(store, output, scope="symbol", symbol="foo")

    payload = _embedded_payload_from_path(output)
    highlighted_names = {n["name"] for n in payload["nodes"] if n["id"] in payload["highlight_ids"]}
    assert highlighted_names == {"foo"}


def test_render_graph_symbol_scope_unknown_symbol_is_empty(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        result = render_graph(store, output, scope="symbol", symbol="nope")
    assert result["node_count"] == 0
    assert result["edge_count"] == 0


def test_render_graph_impact_scope_includes_transitive_caller(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def top():\n    return mid()\n\ndef mid():\n    return low()\n")
    (tmp_path / "b.py").write_text("def low():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    output = tmp_path / ".claude-graph" / "graph.html"

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        result = render_graph(store, output, scope="impact", changed_files=["b.py"], depth=2)

    payload = _embedded_payload_from_path(output)
    names = {n["name"] for n in payload["nodes"]}
    assert "mid" in names
    assert "top" in names
    assert result["node_count"] == len(payload["nodes"])


def test_render_graph_impact_scope_highlights_changed_file_nodes(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("from b import helper\n\ndef main():\n    return helper()\n")
    (tmp_path / "b.py").write_text("def helper():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    output = tmp_path / ".claude-graph" / "graph.html"

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        render_graph(store, output, scope="impact", changed_files=["b.py"])

    payload = _embedded_payload_from_path(output)
    highlighted_names = {n["name"] for n in payload["nodes"] if n["id"] in payload["highlight_ids"]}
    assert "helper" in highlighted_names


def test_render_graph_impact_scope_includes_importer(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("from b import helper\n")
    (tmp_path / "b.py").write_text("def helper():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    build_graph(tmp_path, full_rebuild=True)
    output = tmp_path / ".claude-graph" / "graph.html"

    with GraphStore(tmp_path / ".claude-graph" / "graph.db") as store:
        render_graph(store, output, scope="impact", changed_files=["b.py"])

    payload = _embedded_payload_from_path(output)
    assert any(n["file"] == "a.py" for n in payload["nodes"])


def test_render_graph_missing_symbol_arg_raises(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        try:
            render_graph(store, output, scope="symbol")
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_render_graph_missing_changed_files_arg_raises(tmp_path):
    repo = _make_repo(tmp_path)
    output = repo / ".claude-graph" / "graph.html"
    with GraphStore(repo / ".claude-graph" / "graph.db") as store:
        try:
            render_graph(store, output, scope="impact")
            assert False, "expected ValueError"
        except ValueError:
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_viz.py -v`
Expected: the new `symbol`/`impact` tests FAIL with `NotImplementedError`; the two "missing arg raises" tests PASS already (that validation lives in `render_graph` itself from Task 2).

- [ ] **Step 3: Implement the two functions**

In `claude_graph/viz.py`, replace:

```python
def _symbol_neighborhood(store: GraphStore, symbol: str) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3


def _impact_neighborhood(
    store: GraphStore, changed_files: list[str], depth: int
) -> tuple[list[dict], list[dict], list[int]]:
    raise NotImplementedError  # implemented in Task 3
```

with:

```python
def _symbol_neighborhood(store: GraphStore, symbol: str) -> tuple[list[dict], list[dict], list[int]]:
    targets = list(store.find_nodes_by_name(symbol, kind="function")) + list(
        store.find_nodes_by_name(symbol, kind="class")
    )
    if not targets:
        return [], [], []

    node_rows = {row["id"]: row for row in targets}
    edges: list[dict] = []

    for target in targets:
        for edge in store.edges_by_dst(target["id"], "calls"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": target["id"], "kind": "calls"})
        for edge in store.edges_by_src(target["id"], "calls"):
            dst = store.get_node(edge["dst"])
            if dst is not None:
                node_rows[dst["id"]] = dst
                edges.append({"source": target["id"], "target": dst["id"], "kind": "calls"})
        module = store.find_module_node(target["file"])
        if module is not None:
            node_rows[module["id"]] = module
            for edge in store.edges_by_src(module["id"], "imports"):
                dst = store.get_node(edge["dst"])
                if dst is not None:
                    node_rows[dst["id"]] = dst
                    edges.append({"source": module["id"], "target": dst["id"], "kind": "imports"})

    nodes = [_node_dict(row) for row in node_rows.values()]
    highlight_ids = [t["id"] for t in targets]
    return nodes, edges, highlight_ids


def _impact_neighborhood(
    store: GraphStore, changed_files: list[str], depth: int
) -> tuple[list[dict], list[dict], list[int]]:
    seed_rows = {}
    for file in changed_files:
        for row in store.nodes_for_file(file):
            seed_rows[row["id"]] = row
    if not seed_rows:
        return [], [], []

    node_rows = dict(seed_rows)
    edges: list[dict] = []

    frontier = set(seed_rows.keys())
    seen = set(frontier)
    for _ in range(depth):
        next_frontier: set[int] = set()
        for node_id in frontier:
            for edge in store.edges_by_dst(node_id, "calls"):
                src = store.get_node(edge["src"])
                if src is not None:
                    node_rows[src["id"]] = src
                    edges.append({"source": src["id"], "target": node_id, "kind": "calls"})
                    if src["id"] not in seen:
                        seen.add(src["id"])
                        next_frontier.add(src["id"])
        frontier = next_frontier
        if not frontier:
            break

    for file in changed_files:
        module = store.find_module_node(file)
        if module is None:
            continue
        node_rows[module["id"]] = module
        for edge in store.edges_by_dst(module["id"], "imports"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": module["id"], "kind": "imports"})

    for node_id in list(seed_rows.keys()):
        for edge in store.edges_by_dst(node_id, "tests_for"):
            src = store.get_node(edge["src"])
            if src is not None:
                node_rows[src["id"]] = src
                edges.append({"source": src["id"], "target": node_id, "kind": "tests_for"})

    nodes = [_node_dict(row) for row in node_rows.values()]
    highlight_ids = list(seed_rows.keys())
    return nodes, edges, highlight_ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_viz.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add claude_graph/viz.py tests/test_viz.py
git commit -m "Implement symbol and impact scopes for render_graph"
```

---

### Task 4: CLI `claude-graph viz` command

**Files:**
- Modify: `claude_graph/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `render_graph` (Task 2/3), `GraphStore` (existing), `_resolve_repo_root` (existing, `claude_graph/cli.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_viz_command_full_scope(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    main(["viz", "--repo", str(repo)])
    output = json.loads(capsys.readouterr().out)
    assert (repo / ".claude-graph" / "graph.html").exists()
    assert output["path"] == str(repo / ".claude-graph" / "graph.html")
    assert output["node_count"] > 0


def test_viz_command_custom_output_path(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    custom = repo / "out.html"
    main(["viz", "--repo", str(repo), "-o", str(custom)])
    json.loads(capsys.readouterr().out)
    assert custom.exists()


def test_viz_command_symbol_scope(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    main(["viz", "--repo", str(repo), "--symbol", "foo"])
    output = json.loads(capsys.readouterr().out)
    assert output["node_count"] >= 1


def test_viz_command_impact_scope(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    main(["viz", "--repo", str(repo), "--impact", "a.py"])
    output = json.loads(capsys.readouterr().out)
    assert output["node_count"] >= 1


def test_viz_command_symbol_and_impact_are_mutually_exclusive(tmp_path):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    with pytest.raises(SystemExit):
        main(["viz", "--repo", str(repo), "--symbol", "foo", "--impact", "a.py"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -k viz -v`
Expected: FAIL with `SystemExit` / `argument --repo: invalid choice` style errors, since the `viz` subcommand doesn't exist yet (argparse will reject the unknown subcommand).

- [ ] **Step 3: Implement the command**

In `claude_graph/cli.py`, add the import alongside the existing top-level imports:

```python
from claude_graph.viz import render_graph
```

Then add a new handler function after `_cmd_serve`:

```python
def _cmd_viz(args: argparse.Namespace) -> None:
    repo_root = _resolve_repo_root(args.repo)
    db_path = repo_root / ".claude-graph" / "graph.db"
    output_path = Path(args.output) if args.output else repo_root / ".claude-graph" / "graph.html"

    if args.symbol:
        scope, symbol, changed_files = "symbol", args.symbol, None
    elif args.impact:
        scope, symbol, changed_files = "impact", None, args.impact
    else:
        scope, symbol, changed_files = "full", None, None

    with GraphStore(db_path) as store:
        result = render_graph(
            store, output_path, scope=scope, symbol=symbol, changed_files=changed_files, depth=args.depth
        )
    print(json.dumps(result, indent=2))
```

Then change `build_parser` from:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("build", _cmd_build),
        ("update", _cmd_update),
        ("status", _cmd_status),
        ("install", _cmd_install),
        ("serve", _cmd_serve),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=None, help="Repository root (default: current directory)")
        sub.set_defaults(func=handler)

    return parser
```

to:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-graph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("build", _cmd_build),
        ("update", _cmd_update),
        ("status", _cmd_status),
        ("install", _cmd_install),
        ("serve", _cmd_serve),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=None, help="Repository root (default: current directory)")
        sub.set_defaults(func=handler)

    viz_parser = subparsers.add_parser("viz")
    viz_parser.add_argument("--repo", default=None, help="Repository root (default: current directory)")
    viz_parser.add_argument(
        "-o", "--output", default=None, help="Output HTML path (default: .claude-graph/graph.html)"
    )
    viz_parser.add_argument("--depth", type=int, default=2, help="Impact BFS depth (only used with --impact)")
    viz_group = viz_parser.add_mutually_exclusive_group()
    viz_group.add_argument("--symbol", default=None, help="Render the neighborhood of this function/class name")
    viz_group.add_argument(
        "--impact", nargs="+", default=None, help="Render the impact radius of these changed files"
    )
    viz_parser.set_defaults(func=_cmd_viz)

    return parser
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add claude_graph/cli.py tests/test_cli.py
git commit -m "Add claude-graph viz CLI command"
```

---

### Task 5: MCP `render_graph_tool`

**Files:**
- Modify: `claude_graph/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `render_graph` (Task 2/3).
- Produces: `render_graph_tool(scope: str = "full", symbol: str | None = None, changed_files: list[str] | None = None, depth: int = 2) -> dict`, registered on the MCP server alongside the existing 5 tools.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_server.py`, change the existing `test_lists_expected_tools`:

```python
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
        "render_graph_tool",
    }
```

Append a new test:

```python
def test_render_graph_tool_writes_html(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    _call(app, "build_or_update_graph", {})

    result = _call(app, "render_graph_tool", {})
    assert (repo / ".claude-graph" / "graph.html").exists()
    assert result["path"] == str(repo / ".claude-graph" / "graph.html")
    assert result["node_count"] > 0


def test_render_graph_tool_symbol_scope(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_server(repo)
    _call(app, "build_or_update_graph", {})

    result = _call(app, "render_graph_tool", {"scope": "symbol", "symbol": "bar"})
    assert result["node_count"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: `test_lists_expected_tools` FAILs (set is missing `render_graph_tool`); the two new tests FAIL with an unknown-tool error from the MCP SDK.

- [ ] **Step 3: Implement the tool**

In `claude_graph/mcp_server.py`, add the import at the top alongside the others:

```python
from claude_graph.viz import render_graph
```

And add the tool after `search_nodes_tool` (before `return app`):

```python
    @app.tool()
    def render_graph_tool(
        scope: str = "full",
        symbol: str | None = None,
        changed_files: list[str] | None = None,
        depth: int = 2,
    ) -> dict:
        """Render the graph (or a scoped neighborhood) to a self-contained,
        offline HTML file at .claude-graph/graph.html. `scope` is one of:
        full (default, whole graph), symbol (neighborhood of `symbol`), or
        impact (impact radius of `changed_files` at `depth`). Returns the
        file path — tell the user to open it in a browser; this tool does
        not open it for them."""
        output_path = repo_root / ".claude-graph" / "graph.html"
        with GraphStore(_db_path(repo_root)) as store:
            return render_graph(
                store, output_path, scope=scope, symbol=symbol, changed_files=changed_files, depth=depth
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add claude_graph/mcp_server.py tests/test_mcp_server.py
git commit -m "Expose render_graph as an MCP tool"
```

---

### Task 6: No-network coverage + README

**Files:**
- Modify: `tests/test_no_network.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `render_graph` (Task 2/3), `create_server` (existing).

- [ ] **Step 1: Write the failing test change**

In `tests/test_no_network.py`, add the import:

```python
from claude_graph.viz import render_graph
```

Update `test_full_workflow_makes_no_network_calls` — change:

```python
    # Test MCP server startup (construction registers all 5 tools)
    app = create_server(tmp_path)
    tools = asyncio.run(app.list_tools())
    assert len(tools) == 5
```

to:

```python
        html_result = render_graph(store, tmp_path / ".claude-graph" / "graph.html", scope="full")
        assert html_result["node_count"] > 0

    # Test MCP server startup (construction registers all 6 tools)
    app = create_server(tmp_path)
    tools = asyncio.run(app.list_tools())
    assert len(tools) == 6
```

(This sits inside the existing `with GraphStore(...) as store:` block, right after the `impact = get_impact_radius(...)` line — indent it to match the other `store`-using lines in that block.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_no_network.py -v`
Expected: FAIL — either the old `assert len(tools) == 5` (before the edit) or, once edited, it should pass immediately since `render_graph` was already implemented in Tasks 2-3. Run it once before editing to confirm the old count assertion (`== 5`) is what's there, then apply the edit above.

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_no_network.py -v`
Expected: PASS — confirms `render_graph` makes no network calls even under the socket-blocking fixture.

- [ ] **Step 4: Update the README**

Add a row to the CLI table (after the `serve` row):

```markdown
| `claude-graph viz` | Render an interactive HTML graph view (`--symbol NAME` or `--impact FILE...` to scope it, `-o PATH` to change the output path) |
```

Add a row to the MCP tools table (after `search_nodes_tool`):

```markdown
| `render_graph_tool` | Render the graph (or a scoped neighborhood) to a self-contained local HTML file |
```

Add a new section after "## MCP tools" and before "## Supported languages":

```markdown
## Graph visualization

`claude-graph viz` (or the `render_graph_tool` MCP tool) writes a single
self-contained HTML file to `.claude-graph/graph.html` — open it directly in
a browser via `file://`, no server involved. It embeds a vendored copy of
D3 (ISC license) directly into the file, so it works fully offline, same as
everything else in this tool.

- `claude-graph viz` — the whole graph.
- `claude-graph viz --symbol NAME` — just that function/class's direct
  callers, callees, and its file's imports.
- `claude-graph viz --impact FILE [FILE...] [--depth N]` — the impact
  radius of those changed files (same data as `get_impact_radius_tool`, laid
  out visually).

Click a node to highlight its direct neighborhood and see its file/line in
a side panel; drag to reposition; scroll to zoom; type in the search box to
find a node by name. There's no node cap yet, so a whole-repo view on a very
large codebase (thousands of nodes) may render slowly — see Known
limitations.
```

Add a bullet to "## Known limitations":

```markdown
- `claude-graph viz`'s whole-graph view has no node cap. On a very large
  codebase this can be slow or cluttered in the browser; scope it with
  `--symbol` or `--impact` for a focused view instead.
```

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests PASS (this project's full suite, now including `test_viz.py`)

- [ ] **Step 6: Commit**

```bash
git add tests/test_no_network.py README.md
git commit -m "Cover render_graph in the no-network proof; document viz in README"
```
