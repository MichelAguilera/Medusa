from pathlib import Path

from medusa.model.network import NetworkModel
from medusa.render.templates import render_template


def render_network(
    model: NetworkModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Emit per-host netplan plus a manifest for the cutover role.

    - ``generated/network/<host>/medusa.yaml`` — the static netplan placed
      on the target.
    - ``generated/network-manifest.yaml`` — host -> resolved network config,
      consumed by the `network_cutover` Ansible role for the canonical-IP
      reconnect/identity check.

    The role performs the guarded cutover; this renderer only produces
    config. Both outputs are absent when no host opted into managed
    networking (the manifest still renders with an empty mapping so the role
    can load it unconditionally)."""
    files: dict[Path, str] = {
        generated_dir / "network-manifest.yaml": render_template(
            templates_dir, "network/network-manifest.yaml.j2", {"network": model}
        )
    }
    for host in model.hosts:
        files[generated_dir / "network" / host.name / "medusa.yaml"] = render_template(
            templates_dir, "network/netplan.yaml.j2", {"host": host}
        )
    return files
