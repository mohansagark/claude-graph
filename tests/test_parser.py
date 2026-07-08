from pathlib import Path

from claude_graph.languages import language_for_extension, load_default_languages
from claude_graph.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_python_extracts_functions_and_classes():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    nodes, calls, imports = parse_file(FIXTURES / "sample.py", config)

    names = {(n.kind, n.name) for n in nodes}
    assert ("function", "helper") in names
    assert ("function", "main") in names
    assert ("class", "Greeter") in names
    assert ("function", "greet") in names  # method counts as function


def test_parse_typescript_extracts_functions_and_classes():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.ts")
    nodes, calls, imports = parse_file(FIXTURES / "sample.ts", config)

    names = {(n.kind, n.name) for n in nodes}
    assert ("function", "helper") in names
    assert ("function", "main") in names
    assert ("class", "Greeter") in names
    assert ("function", "greet") in names


def test_parse_python_signature_is_readable():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    nodes, _, _ = parse_file(FIXTURES / "sample.py", config)
    main_node = next(n for n in nodes if n.name == "main")
    assert main_node.signature == "def main(y)"


def test_parse_python_captures_calls_with_enclosing_function():
    configs = load_default_languages()
    config = language_for_extension(configs, "sample.py")
    _, calls, _ = parse_file(FIXTURES / "sample.py", config)
    assert any(c.caller_name == "main" and c.called_name == "helper" for c in calls)


def test_parse_empty_file_returns_empty_lists(tmp_path):
    configs = load_default_languages()
    config = language_for_extension(configs, "empty.py")
    empty = tmp_path / "empty.py"
    empty.write_text("")
    nodes, calls, imports = parse_file(empty, config)
    assert nodes == []
    assert calls == []
    assert imports == []
