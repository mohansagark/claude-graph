import json
import subprocess
from pathlib import Path

import pytest

from claude_graph.cli import main


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    _git("add", "-A", cwd=tmp_path)
    return tmp_path


def test_build_command_creates_graph(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    assert (repo / ".claude-graph" / "graph.db").exists()
    output = json.loads(capsys.readouterr().out)
    assert output["nodes"] >= 1


def test_status_command_reports_no_graph(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["status", "--repo", str(repo)])
    assert "No graph found" in capsys.readouterr().out


def test_update_command_after_build(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["build", "--repo", str(repo)])
    capsys.readouterr()

    (repo / "b.py").write_text("def bar():\n    return 2\n")
    _git("add", "-A", cwd=repo)

    main(["update", "--repo", str(repo)])
    output = json.loads(capsys.readouterr().out)
    assert output["files"] == 2


def test_build_outside_git_repo_exits_with_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["build", "--repo", str(tmp_path)])


def test_install_command_writes_config_and_skills(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    main(["install", "--repo", str(repo)])
    assert (repo / ".mcp.json").exists()
    assert (repo / ".claude" / "skills" / "build-graph" / "SKILL.md").exists()


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
