from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_template(
    templates_dir: Path,
    template_name: str,
    context: dict[str, Any],
) -> str:
    environment = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=False,
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        undefined=StrictUndefined,
    )
    environment.filters["yaml_value"] = yaml_value
    environment.filters["yaml_block"] = yaml_block
    template = environment.get_template(template_name)
    return template.render(**context)


def yaml_value(value: Any, indent: int) -> str:
    if isinstance(value, str):
        return f" {value}"
    if value is None:
        return ""
    if isinstance(value, bool):
        return f" {'true' if value else 'false'}"
    if isinstance(value, int | float):
        return f" {value}"

    return "\n" + yaml_block(value, indent)


def yaml_block(value: Any, indent: int) -> str:
    dumped = yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    ).rstrip()
    padding = " " * indent
    return "\n".join(f"{padding}{line}" for line in dumped.splitlines())
