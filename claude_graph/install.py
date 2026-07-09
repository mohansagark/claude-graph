"""Writes the Claude-Code-only MCP registration and skill files into a
repo. This is the entire 'install' surface of claude-graph: one
repo-local JSON file and two markdown files. No home-directory writes,
no other editor/platform detection, no hooks."""

from __future__ import annotations

import json
from pathlib import Path

BUILD_GRAPH_SKILL = """---
name: build-graph
description: Build or update the claude-graph knowledge graph for this repository.
---

# Build Graph

1. Call `get_graph_stats` to check whether a graph already exists.
2. If it does not exist, call `build_or_update_graph` to run a full build.
3. If it exists, call `build_or_update_graph` to run an incremental update.
4. Report the resulting node/edge/file counts.
"""

REVIEW_CHANGES_SKILL = """---
name: review-changes
description: Review the current uncommitted changes using blast-radius and test-coverage analysis from the claude-graph knowledge graph.
---

# Review Changes

1. Run `git diff --name-only` to find the changed files.
2. Call `get_impact_radius_tool` with those file paths to find affected
   callers, importers, and tests.
3. Call `query_graph_tool` with pattern `file_summary` on each changed
   file to see what functions/classes it defines.
4. Summarize: what changed, what could break (callers/importers), and
   whether covering tests exist. Flag any changed function with no
   corresponding test file.
"""


def _install_mcp_config(repo_root: Path) -> Path:
    config_path = repo_root / ".mcp.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}
    config.setdefault("mcpServers", {})
    config["mcpServers"]["claude-graph"] = {
        "command": "claude-graph",
        "args": ["serve"],
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _install_skills(repo_root: Path) -> list[Path]:
    skills_dir = repo_root / ".claude" / "skills"
    written = []
    for name, content in (("build-graph", BUILD_GRAPH_SKILL), ("review-changes", REVIEW_CHANGES_SKILL)):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")
        written.append(skill_path)
    return written


def install_claude_code(repo_root: Path) -> dict:
    """Writes .mcp.json and .claude/skills/*/SKILL.md into repo_root.
    Idempotent: running twice produces the same result, no duplicate
    entries or files."""
    config_path = _install_mcp_config(repo_root)
    skill_paths = _install_skills(repo_root)
    return {"mcp_config": str(config_path), "skills": [str(p) for p in skill_paths]}
