from pydantic import BaseModel, ConfigDict

from medusa.model.native import NativeSftpShare, NativeSftpUser


class NixosNetwork(BaseModel):
    """Render-ready systemd-networkd config for a NixOS host. Derived from the
    same dns.yaml inputs as the Debian systemd-networkd unit (interface, prefix,
    gateway, nameservers) so both platforms agree on the host's canonical IP.
    ``address`` is the canonical ip joined with the prefix as CIDR. See T-074."""

    model_config = ConfigDict(frozen=True)

    interface: str
    address: str  # canonical ip as CIDR, e.g. "10.0.0.6/24"
    gateway: str
    nameservers: tuple[str, ...]


class NixosMount(BaseModel):
    """One ``fileSystems."<mountpoint>"`` entry. Mirrors the Debian managed
    fstab region exactly -- same source, type, and options -- so a host's NFS
    client mounts are identical whichever platform renders them. Add automount/
    nofail/etc. once in the storage inventory and both platforms inherit it.
    See T-074."""

    model_config = ConfigDict(frozen=True)

    mountpoint: str
    device: str  # "<server-fqdn>:/path"
    fs_type: str  # "nfs" | "nfs4"
    options: tuple[str, ...]


class NixosContainer(BaseModel):
    """One ``virtualisation.oci-containers.containers.<name>`` entry, derived
    from the same ComposeService model the Debian Compose renderer consumes.
    Minimal but real (T-074): image, ports, volumes, environment -- enough to
    prove Docker-on-NixOS hosts are reachable. Richer fields land when a
    container service actually targets a NixOS host."""

    model_config = ConfigDict(frozen=True)

    name: str
    image: str
    ports: tuple[str, ...]
    volumes: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]


class NixosSecret(BaseModel):
    """One ``sops.secrets.<name>`` entry delivered by sops-nix at activation
    (T-077). The reference model is unchanged from the Debian path: the encrypted
    SOPS file is referenced, never its plaintext (Secrets ADR). The host decrypts
    with its own ssh host key (the sops-nix age idiom), so no recipient changes
    are needed beyond listing the host key in `.sops.yaml`. The secret
    materializes at ``/run/secrets/<name>``."""

    model_config = ConfigDict(frozen=True)

    name: str
    # Nix path literal to the encrypted file, relative to the host module
    # (hosts/<host>.nix -> ../secrets/<name>.sops.yaml). The deploy stages the
    # encrypted secrets there so the path stays inside the flake tree (T-078).
    sops_file: str
    owner: str
    mode: str


class NixosHost(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    hostname: str  # networking.hostName
    network: NixosNetwork | None
    file_systems: tuple[NixosMount, ...]
    container_backend: str  # oci-containers backend, "docker" for fleet parity
    containers: tuple[NixosContainer, ...]
    # Host-native SFTP users reshaped into this host's module (T-076). Empty when
    # the host runs no native sftp service. The renderer emits the openssh
    # Match Group + per-user chroot/keys from these.
    sftp_users: tuple[NativeSftpUser, ...]
    # Shared spaces (T-084): pinned-gid groups whose members see a common
    # group-writable mount at shared/<name> inside their chroots. The mounts
    # themselves arrive via file_systems (synthesized into the storage model);
    # this drives the group definitions, memberships, and the sftp umask.
    sftp_shares: tuple[NativeSftpShare, ...] = ()
    # Base dir for the per-user chroots ("<root>/%u" in the Match block). None
    # when the host runs no sftp service. Kept on the model so the template stays
    # formatting-only.
    sftp_chroot_root: str | None
    # services.qemuGuest.enable -- true for "vm" guests (Proxmox/KVM) so the
    # hypervisor can read the guest IP and shut it down gracefully; false for
    # lxc/physical. Derived from the host's nixos_guest. See T-078.
    qemu_guest_agent: bool
    # Whether to emit a systemd-boot bootloader (UEFI). True for vm/physical
    # guests that own a real disk; false for lxc (a container boots from the host
    # kernel, no bootloader). Derived from nixos_guest. See T-078.
    boot_loader: bool
    # system.stateVersion -- the release this host was installed with, pinned for
    # life. See T-078.
    state_version: str
    # Deploy user whose authorized_keys carry admin/deploy SSH access (the host's
    # ansible_user). None when the host has no managed SSH user; then admin_keys
    # is empty and no key block is emitted. See T-078.
    admin_user: str | None
    # Admin/deploy SSH public keys for ``admin_user``. Plain data (not secret).
    # Empty -> no authorizedKeys block (the host won't be reconcilable until the
    # operator adds one). See T-078.
    admin_keys: tuple[str, ...]
    # Import path (relative to hosts/<name>.nix) of the staged disko config, e.g.
    # "../disko/<name>.nix"; None when the host opts out of disko. When set, the
    # host module imports it and the flake adds disko.nixosModules.disko. See
    # T-078.
    disko_module: str | None
    # Verbatim operator-authored disko layout (the contents of
    # ``inventory/nixos/disko/<name>.nix``), carried on the model so the renderer
    # stays a pure formatter -- it writes this into the flake tree rather than
    # reading any file. None when the host opts out of disko. Disk layout is
    # operator territory (T-071/T-072): Medusa never derives a partition scheme.
    disko_source: str | None
    # sops-nix secrets this host's services reference (T-077). Empty when none.
    secrets: tuple[NixosSecret, ...]
    # SSH endpoint for `nixos-rebuild switch --target-host`, as "<user>@<host>".
    # None when the host has no ansible_user (no managed SSH endpoint) -- such a
    # host can be rendered but not reconciled; the deploy plan warns and skips
    # it. Derived in normalize_nixos so deploy dispatch stays model-driven
    # (Platform Fork Boundary, seam 2). See T-075.
    deploy_target: str | None


class NixosModel(BaseModel):
    """Hosts on the NixOS platform, fully derived for the Nix renderer. Empty
    when no host sets ``platform: nixos``. The renderer iterates and formats
    only -- all partitioning and derivation happens in ``normalize_nixos`` so
    the renderer contract holds (NixOS is a new output format, not a new
    architecture). ``nixpkgs_ref`` pins the flake input. See T-073, T-074."""

    model_config = ConfigDict(frozen=True)

    nixpkgs_ref: str
    hosts: tuple[NixosHost, ...]
