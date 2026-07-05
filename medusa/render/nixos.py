from pathlib import Path

from medusa.model.nixos import NixosModel
from medusa.render.compose import _render_compose_file, _render_env_file
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
      systemd-networkd, storage ``fileSystems``, the compose substrate (docker,
      medusa user, sync + per-stack units), and the medusa-secrets unit.
    - ``generated/nixos/stacks/<stack>/…`` -- the host's compose stacks staged
      into the flake tree (T-087). Content is formatted with the SAME compose
      templates/helpers the Debian path uses, from the same models -- compose
      is the platform-neutral container layer; only the delivery differs.
    - ``generated/nixos/secrets-enc/…`` -- ciphertext staged verbatim
      (store-safe; decrypted on the host by medusa-secrets, the T-080 seam).
    - ``generated/nixos/disko/<host>.nix`` -- for hosts that opt into disko
      (``nixos_disko``), the operator-authored layout (carried verbatim on the
      model as ``disko_source``) written into the flake root the host module
      imports (T-078).

    Nothing is emitted when no host is on the NixOS platform, so a pure-Debian
    fleet renders no flake. The model is already fully derived by
    ``normalize_nixos``; this renderer only chooses paths and formats. See
    T-074, T-087."""
    if not model.hosts:
        return {}

    files: dict[Path, str] = {
        generated_dir / "nixos" / "flake.nix": render_template(
            templates_dir, "nixos/flake.nix.j2", {"nixos": model}
        )
    }
    if model.installer_keys:
        # Installer ISO module (T-089): built via the flake's packages output;
        # one generic image per fleet, carrying only the installer keys.
        files[generated_dir / "nixos" / "installer.nix"] = render_template(
            templates_dir, "nixos/installer.nix.j2", {"nixos": model}
        )
    for host in model.hosts:
        files[generated_dir / "nixos" / "hosts" / f"{host.name}.nix"] = (
            render_template(templates_dir, "nixos/host.nix.j2", {"host": host})
        )
        if host.disko_source is not None:
            files[generated_dir / "nixos" / "disko" / f"{host.name}.nix"] = (
                host.disko_source
            )
        for stack in host.stacks:
            stack_dir = generated_dir / "nixos" / "stacks" / stack.name
            files[stack_dir / "docker-compose.yml"] = _render_compose_file(
                stack.compose_file, templates_dir
            )
            for env_file in stack.env_files:
                files[stack_dir / env_file.path] = _render_env_file(env_file)
        for staged in host.staged_secrets:
            files[generated_dir / "nixos" / "secrets-enc" / staged.staged] = (
                staged.ciphertext
            )
    return files
