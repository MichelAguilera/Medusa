from pathlib import Path

from medusa.model.hosts import AnsibleInventoryModel
from medusa.render.templates import render_template


def render_hosts(
    model: AnsibleInventoryModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Emit the ansible inventory + bootstrap helper files for the managed
    hosts. Three outputs:

    - ``generated/ansible/inventory/hosts.ini`` — the ansible static
      inventory consumed by ``ansible-playbook``.
    - ``generated/etc-hosts-bootstrap-block.txt`` — body lines for the
      controller's ``/etc/hosts`` bootstrap block (no marker fences;
      added at apply time by the controller bootstrap Ansible role).
    - ``generated/ssh-config-aliases.txt`` — per-host ``Host`` blocks
      for inclusion in the controller's ``~/.ssh/config``.

    The renderer does not filter, sort, or reshape the managed-host list;
    that is normalize_ansible_inventory's job (per the renderer contract
    in ``CLAUDE.md``).
    """
    context = {
        "managed_hosts": model.managed_hosts,
        "bootstrap_hosts": model.bootstrap_hosts,
    }
    return {
        generated_dir / "ansible" / "inventory" / "hosts.ini": render_template(
            templates_dir, "ansible/hosts.ini.j2", context
        ),
        generated_dir / "etc-hosts-bootstrap-block.txt": render_template(
            templates_dir, "ansible/etc-hosts-bootstrap-block.txt.j2", context
        ),
        generated_dir / "ssh-config-aliases.txt": render_template(
            templates_dir, "ansible/ssh-config-aliases.txt.j2", context
        ),
    }
