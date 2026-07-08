from claude_graph.languages import (
    language_for_extension,
    load_default_languages,
    load_language_config,
)


def test_default_languages_include_python_and_typescript():
    configs = load_default_languages()
    assert "python" in configs
    assert ".py" in configs["python"].extensions
    assert configs["python"].grammar == "python"
    assert "typescript" in configs
    assert ".ts" in configs["typescript"].extensions


def test_language_for_extension_matches_py():
    configs = load_default_languages()
    config = language_for_extension(configs, "src/foo.py")
    assert config is not None
    assert config.name == "python"


def test_language_for_extension_returns_none_for_unknown():
    configs = load_default_languages()
    assert language_for_extension(configs, "foo.rs") is None


def test_repo_override_extends_defaults(tmp_path):
    (tmp_path / ".claude-graph").mkdir()
    (tmp_path / ".claude-graph" / "languages.toml").write_text(
        """
[languages.ruby]
extensions = [".rb"]
grammar = "ruby"
function_node_types = ["method"]
class_node_types = ["class"]
import_node_types = ["call"]
call_node_types = ["call"]
"""
    )
    configs = load_language_config(tmp_path)
    assert "ruby" in configs
    assert "python" in configs  # bundled default still present


def test_repo_override_replaces_matching_language(tmp_path):
    (tmp_path / ".claude-graph").mkdir()
    (tmp_path / ".claude-graph" / "languages.toml").write_text(
        """
[languages.python]
extensions = [".py", ".pyi"]
grammar = "python"
function_node_types = ["function_definition"]
class_node_types = ["class_definition"]
import_node_types = ["import_statement"]
call_node_types = ["call"]
"""
    )
    configs = load_language_config(tmp_path)
    assert ".pyi" in configs["python"].extensions
