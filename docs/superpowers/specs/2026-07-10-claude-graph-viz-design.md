# claude-graph — interactive graph visualization design spec

Date: 2026-07-10

## Purpose

Add an interactive, browser-based visual representation of the graph
`claude-graph` already builds. This was scoped as future work during the v1
design (`docs/superpowers/specs/2026-07-08-claude-graph-design.md`, "Open
items deferred past v1") and is now being picked up.

Today the only way to see graph results is JSON from `query_graph_tool` /
`get_impact_radius_tool` / `search_nodes_tool`. That's fine for Claude Code
to reason over, but there's no way for a human to eyeball the shape of a
codebase's call/import structure, or visually explore "what's around this
function" during a review. This adds that, without compromising the project's
hard requirement: **zero network calls, ever** (see v1 spec's Non-goals).

## Non-goals

- A live/served UI. No HTTP server, no localhost port, no auto-refresh. The
  output is a single static HTML file the user opens directly (`file://`).
- Auto-opening a browser window on the user's behalf. CLI and MCP both just
  write the file and report its path — consistent with the rest of the tool
  never taking surprising automatic action (see v1 spec's no-hooks
  principle).
- A node cap / pagination for very large graphs. Out of scope for v1 of this
  feature; see Known limitations.
- Editing the graph from the visualization (read-only view only).

## Architecture

One new module, `claude_graph/viz.py`, following the same layering as the
rest of the project: it reads from `GraphStore` (no new storage), and is
exposed both from the CLI and as an MCP tool, matching how every other
capability (`query`, `impact`, `search`) is already dual-exposed.

```python
def render_graph(
    store: GraphStore,
    output_path: Path,
    scope: str = "full",       # "full" | "symbol" | "impact"
    symbol: str | None = None,
    changed_files: list[str] | None = None,
    depth: int = 2,
) -> dict:  # {"path": str, "node_count": int, "edge_count": int}
```

- `scope="full"` — every node and edge in the graph. Requires two small
  additions to `GraphStore`: `all_nodes()` and `all_edges()` (every other
  store method today is scoped to a file or a name).
- `scope="symbol"` — resolves `symbol` by name (reusing
  `query.callers_of`/`callees_of`/`imports_of`), rendering that node plus its
  direct (1-hop) neighborhood. This is an ego-network view, not a full
  subgraph traversal.
- `scope="impact"` — reuses `impact.get_impact_radius(store, changed_files,
  depth)` directly; the changed files' nodes are the highlighted seed set,
  the returned callers/importers/tests are the rendered neighborhood.

Invalid combinations (e.g. `scope="symbol"` with no `symbol` given) raise
`ValueError`, matching `query.query_graph`'s existing error style for
unknown patterns.

### CLI

```
claude-graph viz [--symbol NAME | --impact FILE [FILE...]] [--depth N] [-o/--output PATH]
```

- No `--symbol`/`--impact` → `scope="full"`.
- `--symbol` and `--impact` are mutually exclusive (argparse mutually
  exclusive group).
- Default output path: `.claude-graph/graph.html`.
- Prints the same JSON result shape as `build`/`update`/`status`
  (`{"path", "node_count", "edge_count"}`). Does not open a browser.

### MCP tool

```python
@app.tool()
def render_graph_tool(
    scope: str = "full",
    symbol: str | None = None,
    changed_files: list[str] | None = None,
    depth: int = 2,
) -> dict:
    """Render the graph (or a scoped neighborhood) to a self-contained local
    HTML file at .claude-graph/graph.html. Returns its path — tell the user
    to open it in a browser; this tool does not open it for them."""
```

Fixed output path (not caller-choosable over MCP) — keeps the tool's
surface small, same spirit as the other four MCP tools taking minimal
arguments.

## Rendering

- `render_graph` serializes the scoped nodes/edges into a JSON payload:
  ```json
  {
    "nodes": [{"id": 1, "name": "foo", "kind": "function", "file": "a.py", "line": 10}],
    "edges": [{"source": 1, "target": 2, "kind": "calls"}],
    "highlight_ids": [1]
  }
  ```
  `highlight_ids` is empty for `scope="full"`, and is the seed node id(s) for
  `symbol`/`impact` scope.
- This payload is substituted into a static template,
  `claude_graph/static/graph_template.html`, via a plain string replace (a
  `{{DATA_JSON}}` placeholder) — no templating engine dependency.
- The template inlines a vendored copy of `d3.v7.min.js`
  (`claude_graph/static/d3.v7.min.js`, MIT license, license text kept
  alongside it) directly into a `<script>` tag at render time. The output
  HTML file is fully self-contained: no `<script src="http...">`, no
  external stylesheet or font, works opened directly via `file://` with no
  server.

### Visuals

- SVG canvas, D3 force simulation (link + charge + center + collide forces).
- Nodes colored by `kind`: function / class / module get distinct fixed
  colors. Small legend fixed in a corner of the canvas.
- Edges styled by `kind`: `calls` solid, `imports` dashed, `tests_for`
  dotted.

### Interaction

- Pan and zoom (`d3.zoom`).
- Drag individual nodes to reposition them (`d3.drag`); simulation
  re-settles around the dragged position.
- Click a node: dim all other nodes/edges to low opacity, highlight the
  clicked node plus its direct callers/callees/imports (1-hop), and show a
  side panel with its name, kind, file, and line. Click the background to
  clear the highlight back to the full (or scoped) view.
- A client-side text search box filters/pans to matching nodes by name. This
  is pure client-side JS filtering over the already-embedded node list — no
  server round-trip, no re-query.
- For `symbol`/`impact` scope, `highlight_ids` nodes start pre-highlighted
  and the initial viewport auto-fits to them (via a bounding-box zoom-to-fit
  on load) rather than the whole canvas.

## Error handling

- `render_graph` on an empty graph (no build yet) still produces valid HTML
  with an empty canvas and a "no graph data" message, rather than erroring —
  consistent with the v1 tools' existing "silently returns empty" behavior
  before a first build (documented in the README as a known gotcha).
- `scope="symbol"` where `symbol` matches no node: same empty-canvas
  behavior as above, not an error — the user/Claude Code can see the empty
  result and adjust, matching how `callers_of` on an unknown name returns
  `[]` rather than raising.
- Malformed `scope` value: `ValueError`, caught at the CLI layer and printed
  as `error: ...` to stderr (matching `_resolve_repo_root`'s existing error
  style), propagated as-is through the MCP tool (FastMCP surfaces tool
  exceptions to the client).

## Testing

- `tests/test_viz.py`:
  - `render_graph` against a small fixture repo (reusing existing test
    fixtures where possible) produces a well-formed HTML file; the embedded
    JSON payload round-trips (extract and `json.loads` it back out of the
    HTML) and matches expected node/edge counts.
  - Asserts **no `http://` or `https://` substring appears anywhere in the
    output HTML** — the concrete, checkable proof that D3 is truly inlined
    and not CDN-loaded, in the same spirit as the existing no-network test.
  - Covers all three scopes (`full`, `symbol`, `impact`), including the
    empty-result cases above.
  - CLI `viz` command test, in-process, following the existing CLI test
    pattern (`test_cli.py`).
  - MCP `render_graph_tool` test following the existing MCP tool test
    pattern (`test_mcp_server.py`).
- Extend `tests/test_no_network.py` to call `render_graph` under its
  socket-blocking fixture too, so the no-network guarantee explicitly covers
  this new code path (matching how MCP server startup was added to that same
  test in the v1 final review).

## Packaging

- New `claude_graph/static/` directory: `d3.v7.min.js`, its license file, and
  `graph_template.html`.
- `pyproject.toml` gets a new `[tool.hatch.build.targets.wheel.force-include]`
  entry for `claude_graph/static/`, following the existing pattern used for
  `default_languages.toml`.

## Documentation

- README: add `viz` to the CLI table, `render_graph_tool` to the MCP tools
  table, and a short new "Graph visualization" section explaining it's a
  self-contained local HTML file opened directly in a browser — no server
  involved, nothing phones home.

## Known limitations

- No node cap in v1: a whole-repo view (`scope="full"`) on a very large
  codebase (thousands of nodes) may render slowly or feel cluttered in the
  browser. A `--limit`/top-N-by-degree flag is a reasonable v2 addition but
  is deferred — YAGNI until it's actually a problem in practice.
- Vendored `d3.v7.min.js` is a point-in-time copy; picking up upstream D3
  fixes means manually re-vendoring, not an automatic dependency bump.
