from pathlib import Path
from typing import Any

import yaml

IMPORTS_KEY = "imports"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Inventory file does not exist: {path}")

    resolved = path.resolve()
    return _load_resolved(resolved, seen={resolved})


def load_optional_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_yaml(path)


def _load_resolved(path: Path, seen: set[Path]) -> dict[str, Any]:
    body = _read_mapping(path)
    imports = body.pop(IMPORTS_KEY, [])

    if not isinstance(imports, list) or not all(
        isinstance(entry, str) for entry in imports
    ):
        raise ValueError(f"'{IMPORTS_KEY}' must be a list of paths: {path}")

    merged: dict[str, Any] = {}
    for entry in imports:
        fragment_path = (path.parent / entry).resolve()

        if fragment_path in seen:
            raise ValueError(
                f"Inventory file imported more than once: {fragment_path} "
                f"(imports must form a tree; last importer: {path})"
            )
        if not fragment_path.exists():
            raise ValueError(
                f"Imported inventory file does not exist: {fragment_path} "
                f"(imported from {path})"
            )

        seen.add(fragment_path)
        fragment = _load_resolved(fragment_path, seen)
        merged = _merge_mappings(merged, fragment, fragment_path)

    return _merge_mappings(merged, body, path)


def _merge_mappings(
    base: dict[str, Any],
    incoming: dict[str, Any],
    source: Path,
    key_path: str = "",
) -> dict[str, Any]:
    result = dict(base)

    for key, value in incoming.items():
        qualified = f"{key_path}.{key}" if key_path else key

        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_mappings(result[key], value, source, qualified)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            raise ValueError(
                f"Conflicting inventory key '{qualified}' while merging {source} "
                f"(already defined by an earlier fragment)"
            )

    return result


def _read_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as inventory_file:
        loaded = yaml.safe_load(inventory_file)

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Inventory file must contain a YAML mapping: {path}")

    return loaded
