"""Language configuration loading for claude-graph.

Languages are described declaratively (extensions, tree-sitter grammar
name, and the node types that count as a function/class/import/call for
that grammar) so adding a new language is a TOML edit, not a code change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class LanguageConfig:
    name: str
    extensions: tuple[str, ...]
    grammar: str
    function_node_types: tuple[str, ...]
    class_node_types: tuple[str, ...]
    import_node_types: tuple[str, ...]
    call_node_types: tuple[str, ...]


def _parse_languages_toml(data: dict) -> dict[str, LanguageConfig]:
    configs: dict[str, LanguageConfig] = {}
    for name, entry in data.get("languages", {}).items():
        configs[name] = LanguageConfig(
            name=name,
            extensions=tuple(entry["extensions"]),
            grammar=entry["grammar"],
            function_node_types=tuple(entry.get("function_node_types", [])),
            class_node_types=tuple(entry.get("class_node_types", [])),
            import_node_types=tuple(entry.get("import_node_types", [])),
            call_node_types=tuple(entry.get("call_node_types", [])),
        )
    return configs


def load_default_languages() -> dict[str, LanguageConfig]:
    toml_text = (
        resources.files("claude_graph")
        .joinpath("default_languages.toml")
        .read_text(encoding="utf-8")
    )
    return _parse_languages_toml(tomllib.loads(toml_text))


def load_language_config(repo_root: Path) -> dict[str, LanguageConfig]:
    """Bundled defaults, extended/overridden by
    `<repo_root>/.claude-graph/languages.toml` if present. A repo override
    for a language name replaces that language's entry entirely; other
    bundled languages are untouched."""
    configs = dict(load_default_languages())
    override_path = repo_root / ".claude-graph" / "languages.toml"
    if override_path.exists():
        override_data = tomllib.loads(override_path.read_text(encoding="utf-8"))
        configs.update(_parse_languages_toml(override_data))
    return configs


def language_for_extension(
    configs: dict[str, LanguageConfig], file_path: str
) -> LanguageConfig | None:
    suffix = Path(file_path).suffix
    for config in configs.values():
        if suffix in config.extensions:
            return config
    return None
