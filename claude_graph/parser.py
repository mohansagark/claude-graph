"""Tree-sitter based parsing: extracts nodes (functions, classes) and
calls/imports from source files, using the node types declared in a
LanguageConfig so adding a language needs no code change here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node as TSNode
from tree_sitter_language_pack import get_parser

from claude_graph.languages import LanguageConfig


@dataclass
class ParsedNode:
    kind: str  # 'function' | 'class'
    name: str
    start_line: int
    end_line: int
    signature: str


@dataclass
class ParsedCall:
    caller_name: str | None  # None means the call happens at module scope
    called_name: str


@dataclass
class ParsedImport:
    module_text: str


def _find_all(node: TSNode, types: set[str]) -> list[TSNode]:
    matches = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in types:
            matches.append(current)
        stack.extend(reversed(current.children))
    return matches


def _signature(node: TSNode, source: bytes) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte:end].decode("utf-8", errors="replace")
    return text.strip().rstrip(":{").strip()


def _enclosing_function_name(call_node: TSNode, function_types: set[str]) -> str | None:
    """Walk up from a call node to find the innermost function/method
    that contains it, so a call can be attributed to its caller."""
    current = call_node.parent
    while current is not None:
        if current.type in function_types:
            name_node = current.child_by_field_name("name")
            return name_node.text.decode("utf-8", errors="replace") if name_node is not None else None
        current = current.parent
    return None


def _call_target_name(call_node: TSNode) -> str | None:
    function_field = call_node.child_by_field_name("function")
    if function_field is None:
        return None
    if function_field.type == "attribute":  # Python obj.method()
        attr = function_field.child_by_field_name("attribute")
        return attr.text.decode("utf-8", errors="replace") if attr is not None else None
    if function_field.type == "member_expression":  # JS/TS obj.method()
        prop = function_field.child_by_field_name("property")
        return prop.text.decode("utf-8", errors="replace") if prop is not None else None
    return function_field.text.decode("utf-8", errors="replace")


def parse_file(
    path: Path, config: LanguageConfig
) -> tuple[list[ParsedNode], list[ParsedCall], list[ParsedImport]]:
    """Parse one file per its LanguageConfig. Tree-sitter parsers are
    error-tolerant (they emit ERROR nodes rather than raising), so this
    only raises on OSError (e.g. a permission problem reading the file) —
    callers should catch that and skip the file, not the whole build."""
    source = path.read_bytes()
    parser = get_parser(config.grammar)
    tree = parser.parse(source)
    root = tree.root_node

    function_types = set(config.function_node_types)
    class_types = set(config.class_node_types)
    call_types = set(config.call_node_types)
    import_types = set(config.import_node_types)

    nodes: list[ParsedNode] = []
    for fn_node in _find_all(root, function_types):
        name_node = fn_node.child_by_field_name("name")
        if name_node is None:
            continue
        nodes.append(
            ParsedNode(
                kind="function",
                name=name_node.text.decode("utf-8", errors="replace"),
                start_line=fn_node.start_point[0] + 1,
                end_line=fn_node.end_point[0] + 1,
                signature=_signature(fn_node, source),
            )
        )

    for cls_node in _find_all(root, class_types):
        name_node = cls_node.child_by_field_name("name")
        if name_node is None:
            continue
        nodes.append(
            ParsedNode(
                kind="class",
                name=name_node.text.decode("utf-8", errors="replace"),
                start_line=cls_node.start_point[0] + 1,
                end_line=cls_node.end_point[0] + 1,
                signature=_signature(cls_node, source),
            )
        )

    calls: list[ParsedCall] = []
    for call_node in _find_all(root, call_types):
        called_name = _call_target_name(call_node)
        if called_name is None:
            continue
        caller_name = _enclosing_function_name(call_node, function_types)
        calls.append(ParsedCall(caller_name=caller_name, called_name=called_name))

    imports: list[ParsedImport] = []
    for import_node in _find_all(root, import_types):
        module_field = (
            import_node.child_by_field_name("module_name")
            or import_node.child_by_field_name("name")
            or import_node.child_by_field_name("source")
        )
        if module_field is not None:
            text = module_field.text.decode("utf-8", errors="replace").strip("'\"")
            imports.append(ParsedImport(module_text=text))

    return nodes, calls, imports


_TEST_SUFFIXES = (".test", ".spec")


def find_tested_file(test_path: str, all_files: set[str]) -> str | None:
    """Best-effort match of a test file to the file it tests, by naming
    convention only (test_foo.py <-> foo.py, foo_test.py <-> foo.py,
    foo.spec.ts / foo.test.ts <-> foo.ts). Returns None if no convention
    matches or no candidate file exists."""
    p = Path(test_path)
    stem = p.stem
    directory = str(p.parent)

    candidate_stems: list[str] = []
    if stem.startswith("test_"):
        candidate_stems.append(stem[len("test_"):])
    if stem.endswith("_test"):
        candidate_stems.append(stem[: -len("_test")])
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix):
            candidate_stems.append(stem[: -len(suffix)])

    for candidate_stem in candidate_stems:
        for ext in (p.suffix, ".py", ".ts", ".tsx", ".js", ".jsx"):
            candidate_path = str(Path(directory) / f"{candidate_stem}{ext}")
            if candidate_path in all_files:
                return candidate_path
    return None


def resolve_import(importer_file: str, module_text: str, all_files: set[str]) -> str | None:
    """Best-effort resolution of an import's module text to a tracked
    file path, by trying common relative-path (JS/TS) and dotted-package
    (Python) conventions. Returns None rather than guessing when nothing
    tracked matches (e.g. a third-party package)."""
    importer_dir = Path(importer_file).parent

    if module_text.startswith("."):
        base = os.path.normpath(str(importer_dir / module_text))
        candidates = [f"{base}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".py")]
        candidates += [f"{base}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
    else:
        as_path = module_text.replace(".", "/")
        candidates = [
            f"{as_path}.py",
            f"{as_path}/__init__.py",
            f"{as_path}.ts",
            f"{as_path}.tsx",
            f"{as_path}.js",
        ]

    for candidate in candidates:
        if candidate in all_files:
            return candidate
    return None
