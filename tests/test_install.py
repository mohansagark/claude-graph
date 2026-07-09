import json

from claude_graph.install import install_claude_code


def test_install_writes_mcp_config(tmp_path):
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["claude-graph"]["command"] == "claude-graph"
    assert config["mcpServers"]["claude-graph"]["args"] == ["serve"]


def test_install_writes_skills(tmp_path):
    install_claude_code(tmp_path)
    assert (tmp_path / ".claude" / "skills" / "build-graph" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "review-changes" / "SKILL.md").exists()


def test_install_preserves_existing_mcp_config_entries(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other-server": {"command": "other"}}})
    )
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other-server" in config["mcpServers"]
    assert "claude-graph" in config["mcpServers"]


def test_install_is_idempotent(tmp_path):
    install_claude_code(tmp_path)
    install_claude_code(tmp_path)
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert list(config["mcpServers"].keys()) == ["claude-graph"]
