from pathlib import Path

from medusa.model.network import NetworkModel
from medusa.render.templates import render_template


def render_network(
    model: NetworkModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Emit per-host systemd-networkd units plus a manifest for the cutover
    role.

    - ``generated/network/<host>/medusa.network`` — the static
      systemd-networkd unit placed on the target. Medusa standardizes
      managed hosts on systemd-networkd rather than branching per existing
      backend (netplan/ifupdown); the role installs/enables it.
    - ``generated/network-manifest.yaml`` — host -> resolved network config,
      consumed by the `network_cutover` Ansible role for the canonical-IP
      reconnect/identity check.

    The role performs the guarded cutover; this renderer only produces
    config. Per-host units are absent when no host opted into managed
    networking (the manifest still renders with an empty mapping so the role
    can load it unconditionally)."""
    files: dict[Path, str] = {
        generated_dir / "network-manifest.yaml": render_template(
            templates_dir, "network/network-manifest.yaml.j2", {"network": model}
        )
    }
    for host in model.hosts:
        files[
            generated_dir / "network" / host.name / "medusa.network"
        ] = render_template(
            templates_dir, "network/networkd.network.j2", {"host": host}
        )
    return files
