from pathlib import Path

from medusa.model.nixos import NixosModel
from medusa.render.templates import render_template


def render_nixos(
    model: NixosModel,
    templates_dir: Path,
    generated_dir: Path,
) -> dict[Path, str]:
    """Emit the generated NixOS substrate:

    - ``generated/nixos/flake.nix`` -- one shared flake pinning nixpkgs and
      exposing ``nixosConfigurations.<host>`` per NixOS host.
    - ``generated/nixos/hosts/<host>.nix`` -- the per-host module: hostname,
      systemd-networkd, storage ``fileSystems``, and the oci-containers path.
    - ``generated/nixos/disko/<host>.nix`` -- for hosts that opt into disko
      (``nixos_disko``), the operator-authored layout (carried verbatim on the
      model as ``disko_source``) written into the flake root the host module
      imports (T-078). Disk layout is operator territory, so the content is
      passed through untouched -- never rendered or derived (renderer contract
      holds); the file is read at the inventory boundary, not here.

    Nothing is emitted when no host is on the NixOS platform, so a pure-Debian
    fleet renders no flake. The model is already fully derived by
    ``normalize_nixos``; this renderer only chooses paths and formats. See
    T-074."""
    if not model.hosts:
        return {}

    files: dict[Path, str] = {
        generated_dir / "nixos" / "flake.nix": render_template(
            templates_dir, "nixos/flake.nix.j2", {"nixos": model}
        )
    }
    for host in model.hosts:
        files[generated_dir / "nixos" / "hosts" / f"{host.name}.nix"] = (
            render_template(templates_dir, "nixos/host.nix.j2", {"host": host})
        )
        if host.disko_source is not None:
            files[generated_dir / "nixos" / "disko" / f"{host.name}.nix"] = (
                host.disko_source
            )
    return files
