from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Inventory file does not exist: {path}")

    with path.open("r", encoding="utf-8") as inventory_file:
        loaded = yaml.safe_load(inventory_file)

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Inventory file must contain a YAML mapping: {path}")

    return loaded


def load_optional_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_yaml(path)
