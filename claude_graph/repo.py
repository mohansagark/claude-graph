"""Repository root discovery and tracked-file listing for claude-graph."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


class NotAGitRepoError(Exception):
    """Raised when a directory is not inside a git repository."""


def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` to find the nearest directory containing
    `.git`. Raises NotAGitRepoError if none is found."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise NotAGitRepoError(
        f"{start} is not inside a git repository (no .git directory found)"
    )


def list_tracked_files(repo_root: Path) -> list[str]:
    """Git-tracked file paths relative to `repo_root`, filtered by
    `.claude-graphignore` patterns if present."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [line for line in result.stdout.splitlines() if line]
    patterns = _load_ignore_patterns(repo_root)
    if not patterns:
        return files
    return [f for f in files if not _is_ignored(f, patterns)]


def _load_ignore_patterns(repo_root: Path) -> list[str]:
    ignore_file = repo_root / ".claude-graphignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _is_ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
