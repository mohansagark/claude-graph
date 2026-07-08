# claude-graph — design spec

Date: 2026-07-08

## Purpose

A local knowledge graph of a codebase, built specifically for Claude Code on
macOS in a corporate setting. Inspired by the open-source
`code-review-graph` project, but written from scratch, deliberately smaller,
and scoped to a hard requirement: **everything happens locally, with zero
network calls at any point during normal operation.**

Claude Code is the only client this needs to support. There is no
multi-platform install logic, no cloud embeddings, no telemetry, no daemon,
no multi-repo registry.

## Non-goals

- Support for editors/agents other than Claude Code.
- Cloud or local embedding-based semantic search (Claude Code itself, already
  in the loop as the calling agent, does semantic reasoning over the
  candidates this tool returns — no vectors needed).
- Auto-updating hooks that run on every file Write/Edit/Bash. Updates are
  manual/on-demand only, callable by the user or by Claude Code when it
  decides it needs fresh context.
- Multi-repo registry or a background watch daemon.
- Any home-directory (global) config writes. Everything this tool writes
  lives inside the target repository.

## Architecture

Three layers, one Python package (`claude_graph/`):

1. **Parser layer** — Tree-sitter via the `tree_sitter_language_pack`
   dependency (grammars ship with the pip package; no runtime downloads).
   Config-driven: a bundled default `languages.toml` maps file extensions to
   a grammar name plus the tree-sitter node types that count as a function,
   class, import, and call for that language. Adding a language later is a
   config change, not a code change. A project can override/extend this via
   a `.claude-graph/languages.toml` in its own repo, following the same
   schema.

   The bundled default config ships with entries for Python and
   JavaScript/TypeScript/TSX only — covering the languages actually used in
   the user's day-to-day work. Any other language `tree_sitter_language_pack`
   supports can be added per-repo via `.claude-graph/languages.toml` without
   touching the tool's code, per the schema above.

2. **Graph store** — a single SQLite file at `<repo>/.claude-graph/graph.db`.
   One graph per project; no cross-repo state anywhere.

3. **MCP server** — **stdio transport only**. No localhost HTTP port, no
   listening socket. Claude Code launches it as a subprocess and talks to it
   over stdin/stdout, exactly like any other MCP server it manages.

## Components

### CLI (`claude-graph`)

- `claude-graph build` — walks `git ls-files` from the repo root (so
  gitignored files are skipped automatically, matching upstream's
  behavior), parses each tracked file with the matching language config,
  and writes nodes (functions, classes, imports) and edges (calls, imports,
  inheritance, `tests_for`) into SQLite inside one transaction per build.
- `claude-graph update` — hashes currently tracked files against stored
  hashes, re-parses only the changed ones, and removes nodes/edges
  belonging to deleted files. No file-watching; this only runs when
  invoked.
- `claude-graph status` — prints node/edge counts and last-build time.
- `claude-graph install` — the only "install" behavior this tool has:
  writes an MCP server entry into the repo's `.mcp.json` and writes 1-2
  skill files into `.claude/skills/` (`build-graph`, `review-changes`).
  Nothing else. No platform detection, no home-directory writes, no
  settings.json/hooks patching.
- `claude-graph serve` — starts the MCP server (stdio).

### MCP tools exposed

A deliberately small tool surface (not the 30 tools of upstream):

| Tool | Purpose |
|---|---|
| `build_or_update_graph` | Full build if no graph exists, incremental update otherwise |
| `query_graph` | Structural queries: `callers_of`, `callees_of`, `imports_of`, `tests_for`, `file_summary` |
| `get_impact_radius` | Blast radius of a changed file/function: callers + dependents + covering tests |
| `search_nodes` | FTS5 keyword search over function/class names and signatures — no embeddings |
| `get_graph_stats` | Node/edge counts, last build time, languages detected |

### Claude Code integration

- `.mcp.json` in the repo root registers `claude-graph serve` as an MCP
  server (stdio command, no network).
- `.claude/skills/build-graph/SKILL.md` and
  `.claude/skills/review-changes/SKILL.md` give Claude Code slash commands
  for the two main workflows.
- **No hooks.** Nothing auto-executes on file edits or bash calls. Claude
  Code (or the user) calls `build_or_update_graph` explicitly when it wants
  current data.

### Configuration

- `.claude-graphignore` — glob patterns to exclude tracked files from
  indexing (same idea as upstream, needed because git-tracked ≠
  always-relevant, e.g. generated files that are checked in).
- `.claude-graph/languages.toml` — optional per-repo override/extension of
  the default language config.

## Data flow

1. User (or Claude Code) runs `claude-graph build` the first time in a repo.
2. Tree-sitter parses each git-tracked file per its language config;
   extracted nodes/edges are written to `.claude-graph/graph.db`.
3. During a review, Claude Code calls `get_impact_radius` or `query_graph`
   over MCP (stdio) — the server reads SQLite, returns compact JSON.
4. If files changed since the last build, Claude Code (or the user) calls
   `build_or_update_graph` again before querying, or `claude-graph update`
   from the CLI.

At no point in this flow does the process open an outbound network
connection. Parsing, storage, and transport are all local.

## Error handling

- Refuses to operate on a directory that isn't a git repo root (no `.git`)
  — prevents accidentally pointing the tool at `$HOME` or an unintended
  directory.
- A file that fails to parse (unsupported syntax, corrupted content) is
  skipped with a logged warning; it does not fail the whole build.
- All graph writes for a build/update happen inside a single SQLite
  transaction, so an interrupted run can't leave a half-written graph.
- Path resolution stays confined to the validated repo root.

## Testing

- Fixture-based unit tests per language config (small sample files per
  supported language, asserting expected nodes/edges).
- An integration test that builds the graph for a small sample repo and
  checks `get_impact_radius` / `query_graph` results against known-correct
  expectations.
- A **no-network test**: run `build` and the MCP tools with socket
  connections monkeypatched to raise on `connect()`, asserting the whole
  flow completes successfully anyway. This is the concrete, checkable proof
  point for a corporate security review — not just a documentation claim.

## Distribution

- Standard `pyproject.toml` (hatchling build backend), console script
  `claude-graph`.
- Not published to public PyPI. This repo itself (at
  `~/Documents/projects/claude/claude-graph`, pushed to an internal/private
  git remote of the user's choosing) is the distribution artifact.
  Teammates install via `pip install -e .` from a local clone, or
  `pip install git+<internal-repo-url>`.
- A README covering: prerequisites (Python 3.10+, git), install steps,
  `claude-graph install` usage, and the no-network guarantee (pointing at
  the no-network test as evidence).

## Open items deferred past v1

- Additional MCP tools (hub/bridge detection, community detection, wiki
  generation) from upstream were deliberately left out — not needed for
  the core review workflow this is scoped to. Can be added later without
  architectural change if wanted.
- A GitHub Action / CI integration was not requested and is out of scope
  for v1.
