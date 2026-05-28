from pathlib import Path

from medusa.model.groups import AnsibleGroupsModel
from medusa.render.templates import render_template


def render_ansible_groups(
    model: AnsibleGroupsModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    content = render_template(
        templates_dir,
        "ansible/medusa-groups.yml.j2",
        {"ansible": model},
    )
    return {generated_dir / "ansible" / "inventory" / "medusa-groups.yml": content}
