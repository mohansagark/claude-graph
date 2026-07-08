import sys
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


def test_parse_deeply_nested_file_does_not_recursion_error(tmp_path):
    configs = load_default_languages()
    config = language_for_extension(configs, "deep.py")
    depth = sys.getrecursionlimit() + 500
    deep = tmp_path / "deep.py"
    deep.write_text("x = " + "(" * depth + "1" + ")" * depth + "\n")
    nodes, calls, imports = parse_file(deep, config)
    assert nodes == []


from claude_graph.parser import find_tested_file, resolve_import


def test_find_tested_file_python_prefix_convention():
    all_files = {"foo.py", "test_foo.py"}
    assert find_tested_file("test_foo.py", all_files) == "foo.py"


def test_find_tested_file_python_suffix_convention():
    all_files = {"foo.py", "foo_test.py"}
    assert find_tested_file("foo_test.py", all_files) == "foo.py"


def test_find_tested_file_js_spec_convention():
    all_files = {"foo.ts", "foo.spec.ts"}
    assert find_tested_file("foo.spec.ts", all_files) == "foo.ts"


def test_find_tested_file_returns_none_when_no_match():
    all_files = {"test_foo.py"}
    assert find_tested_file("test_foo.py", all_files) is None


def test_resolve_relative_import():
    all_files = {"src/foo.ts", "src/bar.ts"}
    assert resolve_import("src/bar.ts", "./foo", all_files) == "src/foo.ts"


def test_resolve_python_dotted_import():
    all_files = {"pkg/foo.py", "pkg/bar.py", "pkg/__init__.py"}
    assert resolve_import("pkg/bar.py", "pkg.foo", all_files) == "pkg/foo.py"


def test_resolve_import_returns_none_for_third_party():
    all_files = {"pkg/bar.py"}
    assert resolve_import("pkg/bar.py", "requests", all_files) is None
