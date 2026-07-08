import subprocess
from pathlib import Path

import pytest

from claude_graph.repo import NotAGitRepoError, find_repo_root, list_tracked_files


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_find_repo_root_from_nested_dir(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path.resolve()


def test_find_repo_root_raises_outside_git(tmp_path):
    with pytest.raises(NotAGitRepoError):
        find_repo_root(tmp_path)


def test_list_tracked_files(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "b.py").write_text("print(2)\n")
    _git("add", "-A", cwd=tmp_path)
    assert sorted(list_tracked_files(tmp_path)) == ["a.py", "b.py"]


def test_list_tracked_files_respects_ignore_file(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "generated.py").write_text("print(2)\n")
    (tmp_path / ".claude-graphignore").write_text("generated.py\n")
    _git("add", "-A", cwd=tmp_path)
    assert sorted(list_tracked_files(tmp_path)) == [".claude-graphignore", "a.py"]
