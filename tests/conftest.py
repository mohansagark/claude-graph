"""Shared pytest fixtures for claude-graph tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """An empty git repo rooted at tmp_path."""
    _git("init", "-q", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def commit_files():
    """Call with a repo root to stage all files for `git ls-files`
    (no commit needed — `git ls-files` reads the index)."""

    def _stage(repo_root: Path) -> None:
        _git("add", "-A", cwd=repo_root)

    return _stage
